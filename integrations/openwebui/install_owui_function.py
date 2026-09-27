#!/usr/bin/env python3
"""
把 Open WebUI 适配器装进去（幂等）
====================================

装的是一个 **Filter**（`filter.py`），不是 Pipe。区别很关键：

    Pipe   会冒充模型、完全绕过 LLM，用户看到的是检索原文（"检索结果展示器"）
    Filter 挂在真实模型上：inlet 注入资料让 LLM 总结，outlet 确定性追加图片

本脚本做四件事：

  1. 登录管理员；没有账号就注册（Open WebUI 的第一个用户自动成为 admin）
  2. 把 `filter.py` 注册成 **filter** 类型 Function（已存在则更新，重复执行安全）
  3. 确保它处于启用状态、并设为**全局**（否则要在每个模型的设置里单独勾选）
  4. 清掉旧的 Pipe（同名同 id 的那条），免得模型下拉里还留着一个"检索结果展示器"

用法：
    python3 integrations/openwebui/install_owui_function.py
    python3 integrations/openwebui/install_owui_function.py --keep-pipe   # 保留旧 Pipe

环境变量：
    OWUI_URL             Open WebUI 地址     默认 http://localhost:3000
    OWUI_ADMIN_EMAIL     管理员邮箱          默认 admin@mmkb.local
    OWUI_ADMIN_PASS      管理员密码          默认 Mmkb@2026
    OWUI_FUNCTION_ID     Filter id           默认 mmkb_rag
    OWUI_LEGACY_PIPE_ID  旧 Pipe id          默认 mmkb

前置条件：
    Open WebUI 与检索服务都在跑；且 Open WebUI 容器里能访问到检索服务
    （compose 部署由 `MMKB_SEARCH_URL=http://mmkb:8088/search` 提供）。
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
FUNC_ID = os.environ.get("OWUI_FUNCTION_ID", "mmkb_rag")
FUNC_NAME = os.environ.get("OWUI_FUNCTION_NAME", "多模态知识库（检索增强）")
LEGACY_PIPE_ID = os.environ.get("OWUI_LEGACY_PIPE_ID", "mmkb")
FILTER_FILE = Path(__file__).resolve().parent / "filter.py"


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
        print(f"  ✅ 已注册管理员 {EMAIL}（role={r.get('role')}）")
        return r["token"]

    raise SystemExit(
        f"  ❌ 既登录不上也注册不了（HTTP {st}）：{r}\n"
        f"     如果 Open WebUI 已经有别的管理员账号，用 OWUI_ADMIN_EMAIL / "
        f"OWUI_ADMIN_PASS 指定它。"
    )


def install(token: str) -> None:
    if not FILTER_FILE.is_file():
        raise SystemExit(f"  ❌ 找不到 {FILTER_FILE}")
    content = FILTER_FILE.read_text(encoding="utf-8")
    body = {
        "id": FUNC_ID,
        "name": FUNC_NAME,
        "type": "filter",
        "content": content,
        "meta": {"description": "检索自建多模态知识库：资料交给 LLM 总结，插图由代码确定性追加"},
    }

    st, _ = _req("GET", f"/api/v1/functions/id/{FUNC_ID}", token=token)
    if st == 200:
        st, r = _req("POST", f"/api/v1/functions/id/{FUNC_ID}/update", body, token)
        action = "更新"
    else:
        st, r = _req("POST", "/api/v1/functions/create", body, token)
        action = "创建"
    if st != 200:
        raise SystemExit(f"  ❌ {action} Filter 失败（HTTP {st}）：{r}")
    print(f"  ✅ 已{action} Filter「{FUNC_NAME}」(id={FUNC_ID})")

    # 启用（已启用时 toggle 会关掉，所以先读状态）
    st, cur = _req("GET", f"/api/v1/functions/id/{FUNC_ID}", token=token)
    if st == 200 and isinstance(cur, dict) and not cur.get("is_active"):
        st, r = _req("POST", f"/api/v1/functions/id/{FUNC_ID}/toggle", {}, token)
        if st != 200:
            raise SystemExit(f"  ❌ 启用失败（HTTP {st}）：{r}")
        print("  ✅ 已启用")
    else:
        print("  ✅ 已是启用状态")

    # 设为全局（同样先读状态）
    st, cur = _req("GET", f"/api/v1/functions/id/{FUNC_ID}", token=token)
    if st == 200 and isinstance(cur, dict) and not cur.get("is_global"):
        st, r = _req("POST", f"/api/v1/functions/id/{FUNC_ID}/toggle/global", {}, token)
        if st != 200:
            raise SystemExit(f"  ❌ 设为全局失败（HTTP {st}）：{r}")
        print("  ✅ 已设为全局（对所有模型生效）")
    else:
        print("  ✅ 已是全局状态")


def remove_legacy_pipe(token: str) -> None:
    """删掉旧的 Pipe —— 它冒充模型且绕过 LLM，留着只会让人选错。"""
    st, cur = _req("GET", f"/api/v1/functions/id/{LEGACY_PIPE_ID}", token=token)
    if st != 200 or not isinstance(cur, dict):
        print(f"  ✅ 没有旧 Pipe（{LEGACY_PIPE_ID}）需要清理")
        return
    if cur.get("type") != "pipe":
        print(f"  ✅ {LEGACY_PIPE_ID} 不是 Pipe（type={cur.get('type')}），跳过")
        return
    st, r = _req("DELETE", f"/api/v1/functions/id/{LEGACY_PIPE_ID}/delete", token=token)
    if st == 200:
        print(f"  ✅ 已删除旧 Pipe「{cur.get('name')}」(id={LEGACY_PIPE_ID})")
    else:
        print(f"  ⚠️ 删除旧 Pipe 失败（HTTP {st}）：{r}")


def verify(token: str) -> None:
    st, r = _req("GET", "/api/v1/functions/", token=token)
    if st != 200 or not isinstance(r, list):
        raise SystemExit(f"  ❌ 回读 Function 列表失败（HTTP {st}）：{r}")
    hit = next((f for f in r if f.get("id") == FUNC_ID), None)
    if not hit:
        raise SystemExit("  ❌ 回读没找到刚装的 Filter")
    print(f"  ✅ 验证：id={hit['id']} type={hit['type']} "
          f"active={hit['is_active']} global={hit.get('is_global')}")
    if hit.get("type") != "filter":
        raise SystemExit("  ❌ 类型不是 filter，Open WebUI 不会在生成前后调用它")

    st, models = _req("GET", "/api/models", token=token)
    ids = [m.get("id") for m in (models.get("data") or [])] if isinstance(models, dict) else []
    if LEGACY_PIPE_ID in ids:
        print(f"  ⚠️ 模型下拉里仍能看到旧 Pipe「{LEGACY_PIPE_ID}」——建议删除以免选错")
    else:
        print("  ✅ 模型下拉里已没有旧 Pipe")


def main() -> int:
    keep_pipe = "--keep-pipe" in sys.argv
    print(f"== 安装 Open WebUI Filter（{OWUI_URL}）==")
    if not FILTER_FILE.is_file():
        raise SystemExit(f"❌ 找不到 {FILTER_FILE}")
    token = get_token()
    install(token)
    if not keep_pipe:
        remove_legacy_pipe(token)
    verify(token)
    print()
    print("完成。使用方式（与 Pipe 不同，请注意）：")
    print(f"  1. 打开 {OWUI_URL}，在**模型下拉里选一个真实模型**（如 qwen-plus），")
    print(f"     不要再找「{FUNC_NAME}」——Filter 不会出现在模型列表里。")
    print("  2. 提问即可：资料会自动注入给模型总结，插图由代码追加在回答末尾。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
