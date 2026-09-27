"""更完整对比：问题 → (图片描述 | 图注) × (纯向量 | +rerank)"""
import pathlib, json, math, os, time, urllib.request, urllib.error, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
KEY=open("bailian.key").read().strip()
CHAT="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
EMB="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
RER="https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

def post(url, body, timeout=180, retries=3):
    for a in range(retries):
        r=urllib.request.Request(url,data=json.dumps(body).encode(),method="POST")
        r.add_header("Authorization","Bearer "+KEY); r.add_header("Content-Type","application/json")
        try:
            with urllib.request.urlopen(r,timeout=timeout) as resp: return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(5*(a+1)); continue
            raise
    raise RuntimeError("retries")

def embed(model, texts, batch=10):
    out=[]
    for i in range(0,len(texts),batch):
        d=post(EMB,{"model":model,"input":texts[i:i+batch]})
        out+=[x["embedding"] for x in d["data"]]
    return out

def rerank(query, docs, model="qwen3-rerank"):
    d=post(RER,{"model":model,"input":{"query":query,"documents":docs},"parameters":{"top_n":len(docs)}})
    return [x["index"] for x in sorted(d["output"]["results"], key=lambda x:-x["relevance_score"])]

from multimodal_kb.annotate.vlm import VisionAnnotator

_ANNO = None


def _describe(key, path, model=None):
    """用当前包的 VisionAnnotator 生成图片描述。"""
    global _ANNO
    if _ANNO is None:
        _ANNO = VisionAnnotator(key, model or "qwen3-vl-flash")
    return _ANNO.describe(path)


def cos(a,b): return sum(x*y for x,y in zip(a,b))/(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(y*y for y in b)))

d=json.load(open("mineru_out/499cde81-c591-4bca-b372-1161ea1d8d07_content_list_v2.json"))
pairs=[]
for page in d:
    for b in page:
        if b.get("type") in ("image","chart"):
            caps=b["content"].get("image_caption") or []
            t=" ".join(x.get("content","") for x in caps if isinstance(x,dict)).strip()
            p=b["content"].get("image_source",{}).get("path")
            if t and p and os.path.exists("mineru_out/"+p): pairs.append((t,p))
pairs=pairs[:12]
caps=[c for c,_ in pairs]

# 复用已生成的问题与描述（若缓存不存在则重新生成）
cachep=pathlib.Path("_textify_cache.json")
if cachep.exists():
    C=json.loads(cachep.read_text())
else:
    C={"queries":[],"descs":[]}
    print("生成问题与描述 …")
    for cap,p in pairs:
        C["queries"].append(post(CHAT,{"model":"qwen3-vl-flash","messages":[{"role":"user","content":
            f"基于这条图注，写一个用户可能会问的自然问题（中文，不要出现'图注'字样，只输出问题本身）：{cap}"}],"max_tokens":120})["choices"][0]["message"]["content"].strip())
        C["descs"].append(_describe(KEY, pathlib.Path("mineru_out/"+p), di.DEFAULT_MODEL))
    cachep.write_text(json.dumps(C,ensure_ascii=False,indent=2))
Q, D = C["queries"], C["descs"]
n=len(pairs)
print(f"样本 {n} 组\n")

def evaluate(name, docs, use_rerank, emb_model="qwen3.7-text-embedding"):
    dv=embed(emb_model,docs); qv=embed(emb_model,Q)
    h1=0; ranks=[]
    for i,q in enumerate(Q):
        if use_rerank:
            order=rerank(q,docs)
        else:
            order=sorted(range(n), key=lambda j:-cos(qv[i],dv[j]))
        r=order.index(i)+1; ranks.append(r); h1+=(r==1)
    print(f"  {name:42s} Top-1 {h1}/{n} = {h1/n*100:3.0f}%   平均排名 {sum(ranks)/n:.2f}")

print("=== 纯文本检索（问题 → 文档）===")
evaluate("图片描述（VLM 生成）", D, False)
evaluate("图注原文", caps, False)
print("=== 加 rerank ===")
evaluate("图片描述 + qwen3-rerank", D, True)
evaluate("图注原文 + qwen3-rerank", caps, True)
print()
print("=== 对照：跨模态图片检索（同 12 组）===")
print("  multimodal-embedding-v1 文→图               Top-1  40%   平均排名 4.60")
print("  qwen3-vl-embedding 融合向量 文→图            Top-1  95%   平均排名 1.05")

print()
print("=== 最终设计验证：图注 + 图片描述 合并 ===")
merged=[f"{c}。{d}" for c,d in zip(caps,D)]
evaluate("图注+描述（合并）", merged, False)
evaluate("图注+描述（合并）+ rerank", merged, True)
# 更真实的场景：chunk 正文 = 章节路径 + 正文 + 图注 + 描述（模拟真实入库内容）
ctx=[f"【电子工艺实训基础 > 安全用电】{c}。{d}" for c,d in zip(caps,D)]
evaluate("模拟真实 chunk（含章节路径）", ctx, False)
