#!/usr/bin/env python3
"""
把 `pipe.py` 装进 Open WebUI（幂等）
======================================

Open WebUI 的 in-process **Function** 是存在它自己数据库里的，只能通过它自己的
HTTP API（或管理界面）注册 —— 把文件丢进容器目录是不行的。所以这个脚本做三件事：

  1. 登录管理员；没有账号就注册（Open WebUI 的第一个用户自动成为 admin）
  2. 把 `pipe.py` 注册成 Function（**已存在则更新内容**，重复执行安全）
  3. 确保它是启用状态，最后回读一次做验证

用法：
    python3 integrations/openwebui/install_owui_function.py

环境变量：
    OWUI_URL            Open WebUI 地址        默认 http://localhost:3000
    OWUI_ADMIN_EMAIL    管理员邮箱             默认 admin@mmkb.local
    OWUI_ADMIN_PASS     管理员密码             默认 Mmkb@2026
    OWUI_FUNCTION_ID    Function id            默认 mmkb

前置条件：
    Open WebUI 与检索服务都在跑；且 Open WebUI 容器里能访问到检索服务
    （compose 部署由 `MMKB_SEARCH_URL=http://mmkb:8088/search` 提供）。

注：本文件过去是一个**后缀为 .py 的 bash 脚本**（`python3` 直接 SyntaxError），
并且引用了三个不存在的路径（`../.secrets/`、`tools/`、`../run_serve.sh`）。
现已重写为纯标准库的 Python —— 与核心包"零第三方依赖"保持一致。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

OWUI_URL = os.environ.get("OWUI_URL", "http://localhost:3000").rstrip("/")
EMAIL = os.environ.get("OWUI_ADMIN_EMAIL", "admin@mmkb.local")
PASSWORD = os.environ.get("OWUI_ADMIN_PASS", "Mmkb@2026")
FUNC_ID = os.environ.get("OWUI_FUNCTION_ID", "mmkb")
FUNC_NAME = os.environ.get("OWUI_FUNCTION_NAME", "多模态知识库")
PIPE_FILE = Path(__file__).resolve().parent / "pipe.py"


def _req(method: str, path: str, payload=None, token: str | None = None,
         timeout: int = 60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(OWUI_URL + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def get_token() -> str:
    """登录；没有账号就注册（第一个用户是 admin）。"""
    st, r = _req("POST", "/api/v1/auths/signin", {"email": EMAIL, "password": PASSWORD})
    if st == 200 and isinstance(r, dict) and r.get("token"):
        print(f"  ✅ 已登录 {EMAIL}")
        return r["token"]

    st, r = _req("POST", "/api/v1/auths/signup",
                 {"name": "admin", "email": EMAIL, "password": PASSWORD})
    if st == 200 and isinstance(r, dict) and r.get("token"):
        role = r.get("role")
        print(f"  ✅ 已注册管理员 {EMAIL}（role={role}）")
        return r["token"]

    raise SystemExit(
        f"  ❌ 既登录不上也注册不了（HTTP {st}）：{r}\n"
        f"     如果 Open WebUI 已经有别的管理员账号，用 OWUI_ADMIN_EMAIL / "
        f"OWUI_ADMIN_PASS 指定它。"
    )


def install(token: str) -> None:
    if not PIPE_FILE.is_file():
        raise SystemExit(f"  ❌ 找不到 {PIPE_FILE}")
    content = PIPE_FILE.read_text(encoding="utf-8")
    body = {
        "id": FUNC_ID,
        "name": FUNC_NAME,
        "type": "pipe",
        "content": content,
        "meta": {"description": "调用 multimodal-kb 检索服务，确定性注入文本与图片"},
    }

    st, _ = _req("GET", f"/api/v1/functions/id/{FUNC_ID}", token=token)
    if st == 200:
        st, r = _req("POST", f"/api/v1/functions/id/{FUNC_ID}/update", body, token)
        action = "更新"
    else:
        st, r = _req("POST", "/api/v1/functions/create", body, token)
        action = "创建"
    if st != 200:
        raise SystemExit(f"  ❌ {action} Function 失败（HTTP {st}）：{r}")
    print(f"  ✅ 已{action} Function「{FUNC_NAME}」(id={FUNC_ID})")

    # 确保启用（已启用时 toggle 会关掉，所以先读状态）
    st, cur = _req("GET", f"/api/v1/functions/id/{FUNC_ID}", token=token)
    if st == 200 and isinstance(cur, dict) and not cur.get("is_active"):
        st, r = _req("POST", f"/api/v1/functions/id/{FUNC_ID}/toggle", {}, token)
        if st != 200:
            raise SystemExit(f"  ❌ 启用失败（HTTP {st}）：{r}")
        print("  ✅ 已启用")
    else:
        print("  ✅ 已是启用状态")


def verify(token: str) -> None:
    st, r = _req("GET", "/api/v1/functions/", token=token)
    if st != 200 or not isinstance(r, list):
        raise SystemExit(f"  ❌ 回读 Function 列表失败（HTTP {st}）：{r}")
    hit = next((f for f in r if f.get("id") == FUNC_ID), None)
    if not hit:
        raise SystemExit("  ❌ 回读没找到刚装的 Function")
    print(f"  ✅ 验证通过：id={hit['id']} type={hit['type']} active={hit['is_active']}")

    st, models = _req("GET", "/api/models", token=token)
    ids = [m.get("id") for m in (models.get("data") or [])] if isinstance(models, dict) else []
    print(f"  {'✅' if FUNC_ID in ids else '❌'} 模型下拉里{'能看到' if FUNC_ID in ids else '看不到'}「{FUNC_NAME}」")


def main() -> int:
    print(f"== 安装 Open WebUI Function（{OWUI_URL}）==")
    if not PIPE_FILE.is_file():
        raise SystemExit(f"❌ 找不到 {PIPE_FILE}")
    token = get_token()
    install(token)
    verify(token)
    print()
    print(f"完成。打开 {OWUI_URL} → 模型下拉里选「{FUNC_NAME}」即可。")
    print("提示：图片 URL 用的是检索服务的 MMKB_PUBLIC_URL —— 从别的机器访问时，")
    print("      它必须是浏览器能直连的地址（见 docs/deployment.md 6.4）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
