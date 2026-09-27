#!/usr/bin/env python3
"""
端到端验证 Open WebUI 集成（Filter 版）
=========================================

验证的不是"容器起来了"，而是这条链路真的按设计工作：

    问题 → Open WebUI（真实模型 + mmkb_rag Filter）
             ├─ inlet  检索 → 资料注入 system 提示
             ├─ LLM    基于资料总结成通顺回答
             └─ outlet 命中图片确定性追加到回答末尾

五类断言：
  1. 检索服务在跑，且能算出"这个问题应该出现哪几张图"
  2. Filter 已装、已启用、已设为全局，且类型确实是 `filter`
     （类型错了 Open WebUI 根本不会在生成前后调用它）
  3. 走一遍真实模型的对话：拿到非空回答
  4. **回答是"总结"而不是"检索原文堆砌"** —— 这是 Pipe 路线的老毛病，
     也是本脚本最要紧的一条：回答里不应出现检索服务的分块标记
     （`【章节路径】`、`**检索到的相关内容**`）
  5. 确定性图片注入：用同一个问题跑 `filter.py` 的图片收集逻辑，
     命中的每一张图都应能拼出 Markdown，且 URL 真能取到（HTTP 200）

关于第 5 条：图片是在 `outlet` 里追加的，而 Open WebUI 只对**已保存的会话**
把 outlet 的结果落库（需要 `metadata.message_id`，直接调 API 的临时会话没有）。
所以这里验证"确定性拼装 + URL 可达"这一可自动化的部分；
界面里图片实际渲染出来，由 `docs/chat-integration.md` 的截图佐证。

用法：
    python3 integrations/openwebui/verify_integration.py
    Q="电阻的串联有什么特点？" MODEL=qwen-plus python3 integrations/openwebui/verify_integration.py

环境变量：
    OWUI_URL         默认 http://localhost:3000
    MMKB_URL         检索服务，默认 http://localhost:8088
    Q               提问内容，默认「色环电阻怎么读数？」
    MODEL           用哪个模型对话，默认 qwen-plus
    OWUI_ADMIN_EMAIL / OWUI_ADMIN_PASS / OWUI_FUNCTION_ID  同安装脚本
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OWUI_URL = os.environ.get("OWUI_URL", "http://localhost:3000").rstrip("/")
MMKB_URL = os.environ.get("MMKB_URL", "http://localhost:8088").rstrip("/")
QUERY = os.environ.get("Q", "色环电阻怎么读数？")
MODEL = os.environ.get("MODEL", "qwen-plus")
EMAIL = os.environ.get("OWUI_ADMIN_EMAIL", "admin@mmkb.local")
PASSWORD = os.environ.get("OWUI_ADMIN_PASS", "Mmkb@2026")
FUNC_ID = os.environ.get("OWUI_FUNCTION_ID", "mmkb_rag")

MD_IMG = re.compile(r"!\[[^\]]*\]\((https?://[^)]+)\)")
# Pipe 路线会把检索原文直接吐出来，这些是它的特征标记
RAW_MARKERS = ("**检索到的相关内容**", "【")

PASS, FAIL = 0, 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  \033[32m✅\033[0m {msg}")


def bad(msg: str) -> None:
    global FAIL
    FAIL += 1
    print(f"  \033[31m❌\033[0m {msg}")


def note(msg: str) -> None:
    print(f"  \033[2m·\033[0m {msg}")


def req(method: str, url: str, payload=None, token: str | None = None, timeout: int = 240):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        body = resp.read().decode()
        return resp.status, (json.loads(body) if body.strip() else {})


def load_filter_module():
    """把 filter.py 当模块载入（纯标准库，不依赖 Open WebUI）。"""
    path = Path(__file__).resolve().parent / "filter.py"
    spec = importlib.util.spec_from_file_location("mmkb_owui_filter", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    print(f"\n\033[1mOpen WebUI 集成端到端验证（Filter）\033[0m  "
          f"问题：{QUERY} ｜ 模型：{MODEL}")

    # ---- 1) 检索服务 ----
    print("\n\033[1m1/5 检索服务\033[0m")
    try:
        _, hits = req("POST", f"{MMKB_URL}/search",
                      {"query": QUERY, "top_k": 5, "rerank": True})
        results = hits.get("results", [])
        ok(f"{MMKB_URL}/search 返回 {len(results)} 条")
    except Exception as e:
        bad(f"检索服务不可达：{e}")
        return 1

    # ---- 2) Filter 状态 ----
    print("\n\033[1m2/5 Open WebUI Filter\033[0m")
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
            bad(f"Filter {FUNC_ID} 未安装 → 先跑 install_owui_function.py")
            return 1
        if hit.get("type") != "filter":
            bad(f"{FUNC_ID} 的类型是 {hit.get('type')}，必须装成 filter —— "
                f"Pipe 会绕过 LLM，用户只会看到检索原文")
            return 1
        ok(f"Filter「{hit.get('name')}」类型正确")
        if not hit.get("is_active"):
            bad("Filter 未启用")
            return 1
        ok("Filter 已启用")
        if not hit.get("is_global"):
            note("Filter 未设为全局 —— 需要在每个模型的设置里单独勾选才能生效")
        else:
            ok("Filter 已设为全局（对所有模型生效）")
    except Exception as e:
        bad(f"读取 Filter 失败：{e}")
        return 1

    # ---- 3) 真实模型对话 ----
    print("\n\033[1m3/5 通过真实模型提问\033[0m")
    try:
        _, comp = req("POST", f"{OWUI_URL}/api/chat/completions",
                      {"model": MODEL,
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
        bad("回答为空 —— 检查 Filter 的 inlet 是否把请求弄坏了")
        return 1

    # ---- 4) 是"总结"而不是"检索原文" ----
    print("\n\033[1m4/5 回答形态：总结 vs 原文堆砌\033[0m")
    leaked = [m for m in RAW_MARKERS if m in answer]
    if leaked:
        bad(f"回答里出现检索原文标记 {leaked} —— 说明没经过 LLM 总结（退化成 Pipe 了）")
    else:
        ok("回答里没有检索原文的分块标记（是经过模型组织的）")
    if "<img" in answer:
        bad("回答里出现原始 <img> —— Open WebUI 只渲染 ![]()")
    else:
        ok("没有原始 <img>，用的是 Markdown 图片语法")

    # ---- 5) 确定性图片注入 ----
    print("\n\033[1m5/5 确定性图片注入\033[0m")
    try:
        mod = load_filter_module()
        f = mod.Filter()
        images = f.collect_images(results)
        block = f.build_image_block(images)
    except Exception as e:
        bad(f"载入 filter.py 失败：{e}")
        return 1

    if not images:
        note("该问题没有命中图片，只验证了文本链路")
    else:
        if block and "![" in block:
            ok(f"命中的 {len(images)} 张图都能拼成 Markdown 图片区块")
        else:
            bad("命中的图片没能拼成 Markdown 区块")
        good = 0
        for im in images:
            try:
                with urllib.request.urlopen(im["url"], timeout=30) as resp:
                    if resp.status == 200 and resp.read():
                        good += 1
            except Exception:
                pass
        if good == len(images):
            ok(f"{len(images)} 个图片 URL 全部可直连（HTTP 200）")
        else:
            bad(f"{len(images)} 个图片 URL 只有 {good} 个能取到")

    print()
    if FAIL == 0:
        print(f"  \033[32m通过 {PASS} 项 —— Open WebUI 集成链路通 ✅\033[0m")
        print("  \033[2m界面里图片的实际渲染，见 docs/chat-integration.md 的截图。\033[0m\n")
        return 0
    print(f"  \033[31m通过 {PASS} 项，失败 {FAIL} 项\033[0m\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
