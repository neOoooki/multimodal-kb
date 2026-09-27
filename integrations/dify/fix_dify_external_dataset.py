#!/usr/bin/env python3
"""
修复 Dify 外部数据集的 retrieval_model（否则 workflow 检索节点静默返回空）
============================================================================

## 问题现象

在 Dify 里用 **workflow / chatflow 的知识库检索节点**引用**外部知识库**时：

    · 检索节点返回 `{"result": []}`，但节点状态是 `succeeded`（不报错！）
    · 同时，该知识库的「召回测试」完全正常
    · 我们的检索服务**根本没收到请求**

## 根因

1. Dify 的 `POST /datasets/external` 创建外部数据集时**不设置 `retrieval_model`**，
   数据库里该字段为 **NULL**（`ExternalDatasetCreatePayload` 里也没有这个字段）。
2. workflow 检索节点走的是 `DatasetRetrieval._retriever`，它把
   **`dataset.retrieval_model`** 作为 `external_retrieval_parameters` 传给外部服务：

       external_retrieval_parameters = dataset.retrieval_model   # None

3. `fetch_external_knowledge_retrieval` 里直接 `external_retrieval_parameters.get(...)`
   → `AttributeError: 'NoneType' object has no attribute 'get'`
4. 该异常被检索线程的 `skip_on_error=True` **吞掉**，于是节点"成功"返回空列表。

而**召回测试**（`/datasets/{id}/external-hit-testing`）会在请求体里显式带
`external_retrieval_model`，覆盖了 NULL，所以它是好的 —— 这就是"召回测试正常但
chatflow 检索为空"的原因。

## 为什么不能走 API 修

`PATCH /datasets/{id}` 对外部数据集返回 200，但**不持久化 `retrieval_model`**
（Dify 里没有 `ExternalDatasetPatchPayload`，也没有 `update_external_dataset`）。
创建接口同样不接受该字段。所以只能直接改库。

## 用法

    # 需要能直连 Dify 的 Postgres
    python3 fix_dify_external_dataset.py --dataset-id <id>
    python3 fix_dify_external_dataset.py --dataset-name "mmkb-Qdrant融合向量库"

    # 自定义容器/库名
    python3 fix_dify_external_dataset.py --dataset-id <id> --container docker-db_postgres-1 --db dify
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

DEFAULT_RETRIEVAL_MODEL = {
    "search_method": "semantic_search",
    "reranking_enable": False,
    "reranking_mode": None,
    "reranking_model": {"reranking_provider_name": "", "reranking_model_name": ""},
    "weights": None,
    "top_k": 4,
    "score_threshold_enabled": False,
    "score_threshold": 0.5,
}


def psql(container: str, db: str, sql: str) -> str:
    cp = subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", db,
         "-t", "-A", "-c", sql],
        capture_output=True, text=True)
    if cp.returncode != 0:
        raise RuntimeError(f"psql 失败: {cp.stderr.strip()[:300]}")
    return cp.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="修复 Dify 外部数据集的 retrieval_model")
    ap.add_argument("--dataset-id")
    ap.add_argument("--dataset-name")
    ap.add_argument("--container", default="docker-db_postgres-1")
    ap.add_argument("--db", default="dify")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.dataset_id and not args.dataset_name:
        print("需要 --dataset-id 或 --dataset-name", file=sys.stderr)
        return 1

    # 定位数据集
    if args.dataset_id:
        where = f"id='{args.dataset_id}'"
    else:
        where = f"name='{args.dataset_name}'"

    rows = psql(args.container, args.db,
                f"select id, name, provider, coalesce(retrieval_model::text,'NULL') "
                f"from datasets where {where};")
    if not rows:
        print(f"找不到数据集：{where}", file=sys.stderr)
        return 1

    print("匹配到的数据集：")
    targets = []
    for line in rows.splitlines():
        parts = line.split("|")
        if len(parts) < 4:
            continue
        did, name, provider, rm = parts[0], parts[1], parts[2], parts[3]
        flag = "✅ 已有 retrieval_model" if rm != "NULL" else "❌ retrieval_model 为 NULL（workflow 检索会返回空）"
        print(f"  {did}  {name}  provider={provider}  {flag}")
        if provider == "external" and rm == "NULL":
            targets.append(did)

    if not targets:
        print("\n无需修复。")
        return 0

    rm = dict(DEFAULT_RETRIEVAL_MODEL)
    rm["top_k"] = args.top_k
    payload = json.dumps(rm, ensure_ascii=False).replace("'", "''")

    print(f"\n将给 {len(targets)} 个外部数据集写入 retrieval_model：")
    print(json.dumps(rm, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("\n--dry-run：未执行")
        return 0

    for did in targets:
        psql(args.container, args.db,
             f"update datasets set retrieval_model='{payload}'::jsonb where id='{did}';")
        now = psql(args.container, args.db,
                   f"select coalesce(left(retrieval_model::text,60),'NULL') from datasets where id='{did}';")
        print(f"  ✅ {did} → {now}")

    print("\n修复完成。请重新运行 workflow/chatflow 验证检索节点不再返回空。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
