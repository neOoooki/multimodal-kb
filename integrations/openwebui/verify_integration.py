#!/usr/bin/env python3
"""
端到端验证 Open WebUI 集成
============================

验证的不是"容器起来了"，而是**这条链路真的通**：

    问题 → Open WebUI（model=mmkb，即 pipe.py）→ 检索服务 → 回答里带图片

四类断言：
  1. Open WebUI 在跑、Function 已装且已启用
  2. 通过 `/api/chat/completions` 走一遍 pipe，拿到非空回答
  3. 回答里**不含**原始 `<img>`（Open WebUI 不渲染它，只认 `![]()`）
  4. 检索服务为该问题命中的**每一张图**都出现在回答里，且 URL 真能取到（HTTP 200）

第 4 条是"确定性注入"的核心契约：图片由代码写进流，不经过 LLM。
`docs/chat-integration.md` 里那张"指向检索服务的 <img>: 3 / 加载成功=true"的截图，
对应的就是这条断言。

用法：
    python3 integrations/openwebui/verify_integration.py
    Q="接地保护示意图" python3 integrations/openwebui/verify_integration.py

环境变量：
    OWUI_URL         默认 http://localhost:3000
    MMKB_URL         检索服务，默认 http://localhost:8088
    Q               提问内容，默认「万用表怎么用？」
    OWUI_ADMIN_EMAIL / OWUI_ADMIN_PASS / OWUI_FUNCTION_ID  同安装脚本
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

OWUI_URL = os.environ.get("OWUI_URL", "http://localhost:3000").rstrip("/")
MMKB_URL = os.environ.get("MMKB_URL", "http://localhost:8088").rstrip("/")
QUERY = os.environ.get("Q", "万用表怎么用？")
EMAIL = os.environ.get("OWUI_ADMIN_EMAIL", "admin@mmkb.local")
PASSWORD = os.environ.get("OWUI_ADMIN_PASS", "Mmkb@2026")
FUNC_ID = os.environ.get("OWUI_FUNCTION_ID", "mmkb")

MD_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")
PASS, FAIL = 0, 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32m✅\033[0m {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31m❌\033[0m {msg}")


def req(method: str, url: str, payload=None, token: str | None = None, timeout: int = 180):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read().decode()
        return resp.status, (json.loads(body) if body.strip() else {})


def main() -> int:
    print(f"\n\033[1mOpen WebUI 集成端到端验证\033[0m  （问题：{QUERY}）")

    # ---- 1) 检索服务 ----
    print("\n\033[1m1/4 检索服务\033[0m")
    try:
        _, hits = req("POST", f"{MMKB_URL}/search",
                      {"query": QUERY, "top_k": 5, "rerank": True})
        results = hits.get("results", [])
        ok(f"{MMKB_URL}/search 返回 {len(results)} 条")
    except Exception as e:
        bad(f"检索服务不可达：{e}")
        return 1

    expected = []
    for h in results:
        for im in (h.get("images") or []):
            u = im.get("url")
            if u and u not in expected:
                expected.append(u)
        for u in MD_IMG.findall(h.get("content") or ""):
            if u not in expected:
                expected.append(u)
    if expected:
        ok(f"该问题命中 {len(expected)} 张图（这就是回答里应该出现的）")
    else:
        print("  \033[2m·\033[0m 该问题没有命中图片，只验证文本链路")

    # ---- 2) Open WebUI + Function ----
    print("\n\033[1m2/4 Open WebUI Function\033[0m")
    try:
        _, auth = req("POST", f"{OWUI_URL}/api/v1/auths/signin",
                      {"email": EMAIL, "password": PASSWORD})
        token = auth["token"]
        ok(f"已登录 {EMAIL}")
    except Exception as e:
        bad(f"登录失败（先跑 install_owui_function.py）：{e}")
        return 1

    try:
        _, fns = req("GET", f"{OWUI_URL}/api/v1/functions/", token=token)
        hit = next((f for f in fns if f.get("id") == FUNC_ID), None)
        if not hit:
            bad(f"Function {FUNC_ID} 未安装 → 先跑 install_owui_function.py")
            return 1
        if not hit.get("is_active"):
            bad(f"Function {FUNC_ID} 未启用")
            return 1
        ok(f"Function「{hit.get('name')}」已安装且已启用")
    except Exception as e:
        bad(f"读取 Function 失败：{e}")
        return 1

    # ---- 3) 走一遍 pipe ----
    print("\n\033[1m3/4 通过 Open WebUI 提问（model=mmkb）\033[0m")
    try:
        _, comp = req("POST", f"{OWUI_URL}/api/chat/completions",
                      {"model": FUNC_ID,
                       "messages": [{"role": "user", "content": QUERY}],
                       "stream": False}, token)
        answer = (comp.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except urllib.error.HTTPError as e:
        bad(f"chat/completions 失败：HTTP {e.code} {e.read().decode()[:200]}")
        return 1
    except Exception as e:
        bad(f"chat/completions 失败：{e}")
        return 1

    if answer.strip():
        ok(f"拿到回答（{len(answer)} 字）")
    else:
        bad("回答为空")
        return 1

    if "<img" in answer:
        bad("回答里出现原始 <img> —— Open WebUI 只会把它当纯文本，必须用 ![]()")
    else:
        ok("回答里没有原始 <img>（用的是 Markdown 图片语法）")

    # ---- 4) 确定性注入：命中的图必须出现，且能取到 ----
    print("\n\033[1m4/4 确定性图片注入\033[0m")
    in_answer = MD_IMG.findall(answer)
    missing = [u for u in expected if u not in answer]
    if not expected:
        print("  \033[2m·\033[0m 无图片可校验，跳过")
    elif missing:
        bad(f"{len(missing)}/{len(expected)} 张命中图片没出现在回答里：{missing[0]}")
    else:
        ok(f"命中的 {len(expected)} 张图全部出现在回答里")

    if in_answer:
        good = 0
        for u in in_answer:
            try:
                with urllib.request.urlopen(u, timeout=30) as resp:
                    if resp.status == 200 and resp.read():
                        good += 1
            except Exception:
                pass
        if good == len(in_answer):
            ok(f"回答里 {len(in_answer)} 个图片 URL 全部可直连（HTTP 200）")
        else:
            bad(f"回答里 {len(in_answer)} 个图片 URL 只有 {good} 个能取到")
    elif expected:
        bad("回答里没有任何图片 URL")

    print()
    if FAIL == 0:
        print(f"  \033[32m通过 {PASS} 项 —— Open WebUI 集成链路通 ✅\033[0m\n")
        return 0
    print(f"  \033[31m通过 {PASS} 项，失败 {FAIL} 项\033[0m\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
