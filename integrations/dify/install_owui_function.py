#!/usr/bin/env python3
"""
把 Pipe Function 装进 Open WebUI
==================================

Open WebUI 的 Function 存在它自己的数据库里，通过 API 创建：
    POST /api/v1/functions/create
    body: {id, name, type: "pipe", content: "<python 源码>", meta: {...}}

装完还要在「管理员设置 → 函数」里**启用**（`is_active`），否则模型列表里看不到。
"""
import json, os, sys, urllib.request, urllib.error
from pathlib import Path

BASE = os.environ.get("OWUI_URL", "http://localhost:3000")
TOKEN = os.environ.get("OWUI_TOKEN", "")
PIPE = Path(__file__).resolve().parents[1] / "adapters" / "openwebui" / "pipe.py"


def req(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if TOKEN:
        r.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body or "{}")
        except Exception:
            return e.code, {"raw": body[:300]}


def main() -> int:
    src = PIPE.read_text(encoding="utf-8")
    fid = "mmkb_multimodal_kb"
    body = {
        "id": fid,
        "name": "多模态知识库",
        "meta": {"description": "自建：MinerU 两级分块 + 三路向量 + RRF + rerank，图片由代码确定性注入",
                 "manifest": {}},
        "type": "pipe",
        "content": src,
    }
    st, r = req("POST", "/api/v1/functions/create", body)
    if st not in (200, 201):
        print("创建失败:", st, json.dumps(r, ensure_ascii=False)[:300])
        return 1
    print("✅ Function 已创建:", fid)

    # 启用（否则模型下拉里看不到）
    st, r = req("POST", f"/api/v1/functions/id/{fid}/toggle")
    print("切换启用:", st, json.dumps(r, ensure_ascii=False)[:120])

    st, r = req("GET", "/api/v1/functions/")
    items = r if isinstance(r, list) else r.get("data") or []
    for f in items:
        if f.get("id") == fid:
            print(f"状态: is_active={f.get('is_active')}  type={f.get('type')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
