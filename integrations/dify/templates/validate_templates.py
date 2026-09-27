#!/usr/bin/env python3
"""
Dify 知识流水线 DSL 离线校验器
================================
在导入 Dify 之前，先在本机把模板结构检查一遍，避免导入后才发现连线/变量引用写错。

校验内容:
  1. YAML 可解析，顶层 kind=rag_pipeline、version 合法
  2. 节点 id 唯一；每条边的 source/target 都指向真实节点
  3. 图连通性：没有孤立节点
  4. knowledge-index 节点的 index_chunk_variable_selector 指向真实节点
  5. 所有 {{#node.var#}} 模板引用都指向真实节点
  6. {{#rag.shared.X#}} 引用都能在 rag_pipeline_variables 里找到
  7. dependencies 里的插件在本机 Dify 中是否已安装（可选提示，不阻塞）

用法:
    python3 validate_templates.py                    # 校验当前目录所有 *.yml
    python3 validate_templates.py path/to/file.yml   # 校验指定文件
"""

from __future__ import annotations

import glob
import os
import re
import sys

import yaml

# 匹配 {{#node_id.var#}} 和 {{#rag.shared.var#}}
VARIABLE_REF = re.compile(r"\{\{#([^#{}]+?)#\}\}")
# 本机 Dify 插件安装目录（用于依赖检查）
PLUGIN_DIRS = [
    os.path.expanduser("~/dify/docker/volumes/plugin_daemon/plugin_packages"),
    os.path.expanduser("~/dify/docker/volumes/plugin_daemon/plugin"),
]

SUPPORTED_DSL_VERSIONS = {"0.1.0"}


class Check:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def installed_plugin_ids() -> set[str]:
    """收集本机已安装插件的唯一标识。"""
    found: set[str] = set()
    for base in PLUGIN_DIRS:
        if not os.path.isdir(base):
            continue
        for author in os.listdir(base):
            author_dir = os.path.join(base, author)
            if not os.path.isdir(author_dir):
                continue
            for name in os.listdir(author_dir):
                # 目录/文件名形如 general_chunker:0.0.14@<hash>
                found.add(f"{author}/{name}")
    return found


def _validate_app(path: str, data: dict, c: Check) -> Check:
    """校验 App DSL（kind: app）——目前覆盖 chatflow 的结构与变量引用。"""
    version = data.get("version")
    if not isinstance(version, str):
        c.error(f"version 必须是字符串，实际为 {version!r}")
    app = data.get("app")
    if not isinstance(app, dict):
        c.error("缺少 app 元数据块")
        return c
    mode = app.get("mode")
    if not app.get("name"):
        c.warn("app.name 为空")
    if mode not in {"chat", "advanced-chat", "agent-chat", "workflow", "completion"}:
        c.error(f"app.mode 非法: {mode!r}")

    workflow = data.get("workflow")
    if not isinstance(workflow, dict):
        c.error("缺少 workflow 块")
        return c

    graph = workflow.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        c.error("graph.nodes 为空")
        return c

    node_ids = [str(n.get("id")) for n in nodes if n.get("id")]
    if len(node_ids) != len(set(node_ids)):
        c.error("节点 id 重复")
    idset = set(node_ids)

    for e in edges:
        for key in ("source", "target"):
            if str(e.get(key)) not in idset:
                c.error(f"边 {e.get('id')!r} 的 {key}={e.get(key)!r} 不指向任何节点")

    # 变量引用检查 —— 这是最容易出错的地方：
    # Dify 要求 {{#节点ID.变量#}}，写成节点标题会被当成普通字符串原样输出。
    # 保留字：系统变量前缀 + LLM 节点自己的占位符（不是节点引用）
    reserved = {
        "sys", "env", "conversation", "rag",     # 系统/环境/会话/流水线共享变量
        "context", "context_files",              # LLM 节点的内置占位符
    }
    for n in nodes:
        blob = yaml.dump(n.get("data", {}), allow_unicode=True)
        for m in VARIABLE_REF.finditer(blob):
            ref = m.group(1)
            head = ref.split(".")[0]
            if head in reserved:
                continue
            if head not in idset:
                c.error(
                    f"节点 {n.get('id')} 引用了不存在的节点: {{{{{{{ref}}}}}}}"
                    f"（注意：必须用节点 ID，不能用节点标题）"
                )

    # chatflow 多模态的两个关键开关
    types = {n.get("data", {}).get("type"): n for n in nodes}
    kr = types.get("knowledge-retrieval")
    llm = types.get("llm")
    if kr is not None and not (kr["data"].get("vision") or {}).get("enabled"):
        c.warn("knowledge-retrieval.vision.enabled 不是 true，检索结果不会带出图片")
    if kr is not None and llm is not None and not (llm["data"].get("vision") or {}).get("enabled"):
        c.warn(
            "llm.vision.enabled 不是 true —— 即使检索到了图片，"
            "graphon 的 _append_file_prompts() 也会静默丢弃"
        )
    return c


def validate(path: str, installed: set[str]) -> Check:
    c = Check()
    with open(path, encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            c.error(f"YAML 解析失败: {exc}")
            return c

    if not isinstance(data, dict):
        c.error("顶层必须是映射（mapping）")
        return c

    kind = data.get("kind")
    if kind == "app":
        return _validate_app(path, data, c)
    if kind != "rag_pipeline":
        c.error(f"kind 必须是 rag_pipeline 或 app，实际为 {kind!r}")
        return c

    # --- 1. 顶层结构 ---
    version = data.get("version")
    if not isinstance(version, str):
        c.error(f"version 必须是字符串，实际为 {version!r}（应为 '0.1.0'）")
    elif version not in SUPPORTED_DSL_VERSIONS:
        c.warn(f"version={version} 不在已验证列表 {sorted(SUPPORTED_DSL_VERSIONS)} 中")

    rp = data.get("rag_pipeline")
    if not isinstance(rp, dict):
        c.error("缺少 rag_pipeline 元数据块")
        return c
    if not rp.get("name"):
        c.warn("rag_pipeline.name 为空，导入后知识库名可能为 Untitled")

    workflow = data.get("workflow")
    if not isinstance(workflow, dict):
        c.error("缺少 workflow 块")
        return c

    graph = workflow.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        c.error("graph.nodes 为空")
        return c

    # --- 2. 节点与边 ---
    node_ids: list[str] = []
    for n in nodes:
        nid = n.get("id")
        if not nid:
            c.error(f"存在没有 id 的节点: {n.get('data', {}).get('title')}")
            continue
        node_ids.append(str(nid))
    if len(node_ids) != len(set(node_ids)):
        dupes = {i for i in node_ids if node_ids.count(i) > 1}
        c.error(f"节点 id 重复: {sorted(dupes)}")
    idset = set(node_ids)

    ok_data = [n for n in nodes if n.get("data", {}).get("type")]
    if len(ok_data) != len(nodes):
        c.error("存在缺少 data.type 的节点")

    for e in edges:
        for key in ("source", "target"):
            if str(e.get(key)) not in idset:
                c.error(f"边 {e.get('id')!r} 的 {key}={e.get(key)!r} 不指向任何节点")

    # --- 3. 连通性 ---
    connected = {str(e.get("source")) for e in edges} | {str(e.get("target")) for e in edges}
    isolated = [i for i in node_ids if i not in connected]
    if isolated:
        c.warn(f"孤立节点（无任何连线）: {isolated}")

    # --- 4/5. 变量引用 ---
    shared_vars = {v.get("variable") for v in (workflow.get("rag_pipeline_variables") or [])}
    refs: set[str] = set()
    for n in nodes:
        blob = yaml.dump(n.get("data", {}), allow_unicode=True)
        for m in VARIABLE_REF.finditer(blob):
            refs.add(m.group(1))

        if n.get("data", {}).get("type") == "knowledge-index":
            sel = n["data"].get("index_chunk_variable_selector") or []
            if not sel:
                c.error("knowledge-index 节点缺少 index_chunk_variable_selector")
            elif str(sel[0]) not in idset:
                c.error(f"knowledge-index 的 index_chunk_variable_selector 指向不存在的节点 {sel[0]!r}")
            cs = n["data"].get("chunk_structure")
            if cs not in {"text_model", "hierarchical_model"}:
                c.warn(f"chunk_structure={cs!r} 不是常见值（text_model / hierarchical_model）")

    for ref in sorted(refs):
        parts = ref.split(".")
        if parts[0] == "rag" and len(parts) >= 3 and parts[1] == "shared":
            if parts[2] not in shared_vars:
                c.error(f"引用了未声明的共享变量: {ref}")
        elif parts[0] not in idset:
            c.error(f"引用了不存在的节点: {ref}")

    # --- 6. 依赖 ---
    deps = data.get("dependencies") or []
    if not deps:
        c.warn("没有声明 dependencies（若使用插件节点，导入时可能缺少依赖）")
    for d in deps:
        val = d.get("value") or {}
        dep = val.get("marketplace_plugin_unique_identifier") or val.get("plugin_unique_identifier")
        if not dep:
            c.error(f"依赖项缺少标识: {d}")
            continue
        if installed and dep not in installed:
            c.warn(f"本机未见该插件（导入时 Dify 会提示安装）: {dep}")

    return c


def main() -> int:
    targets = sys.argv[1:] or sorted(glob.glob("*.yml"))
    if not targets:
        print("没有找到 .yml 模板")
        return 1

    installed = installed_plugin_ids()
    if not installed:
        print("提示: 未找到本机 Dify 插件目录，跳过依赖安装检查\n")

    total_errors = 0
    for path in targets:
        check = validate(path, installed)
        status = "✅ 通过" if not check.errors else "❌ 失败"
        print(f"{status}  {path}")
        for w in check.warnings:
            print(f"   ⚠️  {w}")
        for e in check.errors:
            print(f"   ❌ {e}")
        total_errors += len(check.errors)
        print()

    print("=" * 50)
    print("全部模板校验通过 ✅" if total_errors == 0 else f"共 {total_errors} 个错误 ❌")
    return 1 if total_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
