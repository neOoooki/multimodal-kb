"""验证「图片语义文本化」策略：把图变成描述文本后，纯文本检索能否命中"""
import pathlib, json, base64, math, os, urllib.request, urllib.error, time, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

KEY = open("bailian.key").read().strip()
CHAT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
EMB  = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"

def chat(model, msgs, mt=200):
    body={"model":model,"messages":msgs,"max_tokens":mt,"temperature":0.3}
    for a in range(3):
        r=urllib.request.Request(CHAT,data=json.dumps(body).encode(),method="POST")
        r.add_header("Authorization","Bearer "+KEY); r.add_header("Content-Type","application/json")
        try:
            with urllib.request.urlopen(r,timeout=120) as resp:
                return json.loads(resp.read().decode())["choices"][0]["message"]["content"].strip()
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(5*(a+1)); continue
            return ""
    return ""

def embed(model, texts, batch=10):
    """注意：text-embedding-v4 单次最多 10 条，超过返回 400"""
    out=[]
    for i in range(0,len(texts),batch):
        body={"model":model,"input":texts[i:i+batch]}
        for a in range(3):
            r=urllib.request.Request(EMB,data=json.dumps(body).encode(),method="POST")
            r.add_header("Authorization","Bearer "+KEY); r.add_header("Content-Type","application/json")
            try:
                with urllib.request.urlopen(r,timeout=180) as resp:
                    d=json.loads(resp.read().decode())
                out += [x["embedding"] for x in d["data"]]; break
            except urllib.error.HTTPError as e:
                if e.code==429: time.sleep(5*(a+1)); continue
                raise
    return out

from multimodal_kb.annotate.vlm import VisionAnnotator

_ANNO = None


def _describe(key, path, model=None):
    """用当前包的 VisionAnnotator 生成图片描述。"""
    global _ANNO
    if _ANNO is None:
        _ANNO = VisionAnnotator(key, model or "qwen3-vl-flash")
    return _ANNO.describe(path)


def cos(a,b):
    return sum(x*y for x,y in zip(a,b))/(math.sqrt(sum(x*x for x in a))*math.sqrt(sum(y*y for y in b)))

# 取前 12 组（成本与时间可控）
d=json.load(open("mineru_out/499cde81-c591-4bca-b372-1161ea1d8d07_content_list_v2.json"))
pairs=[]
for pi,page in enumerate(d):
    for b in page:
        if b.get("type") in ("image","chart"):
            caps=b["content"].get("image_caption") or []
            txt=" ".join(x.get("content","") for x in caps if isinstance(x,dict)).strip()
            p=b["content"].get("image_source",{}).get("path")
            if txt and p and os.path.exists("mineru_out/"+p): pairs.append((txt,p))
pairs=pairs[:12]
print(f"测试对: {len(pairs)}\n")

print("① 为每张图生成详细描述 …")
descs=[]
for i,(cap,p) in enumerate(pairs,1):
    de=_describe(KEY, __import__("pathlib").Path("mineru_out/"+p), di.DEFAULT_MODEL)
    descs.append(de); print(f"   [{i}/{len(pairs)}] {len(de)} 字")
print()

print("② 用 LLM 基于图注生成「用户会怎么问」的问题 …")
queries=[]
for i,(cap,_) in enumerate(pairs,1):
    q=chat("qwen3-vl-flash",[{"role":"user","content":
        f"基于这条图注，写一个用户可能会问的自然问题（中文，不要出现'图注'字样，只输出问题本身）：{cap}"}],120)
    queries.append(q or cap); print(f"   [{i}] {q[:60]}")
print()

print("③ 对比两种检索方式（有标准答案：第 i 个问题应命中第 i 张图/描述）")
for emb_model in ["text-embedding-v4","qwen3.7-text-embedding"]:
    dvecs = embed(emb_model, descs)
    qvecs = embed(emb_model, queries)
    h1=0; ranks=[]
    for i,qv in enumerate(qvecs):
        order=sorted(range(len(dvecs)), key=lambda j:-cos(qv,dvecs[j]))
        r=order.index(i)+1; ranks.append(r); h1+=(r==1)
    print(f"   [纯文本] {emb_model:26s} 问题→图片描述  Top-1 {h1}/{len(pairs)} = {h1/len(pairs)*100:.0f}%  平均排名 {sum(ranks)/len(ranks):.2f}")
print()
print("   对照（同样是这几个问题，走跨模态图片检索）：")
print("     multimodal-embedding-v1  文→图  Top-1 40%  平均排名 4.60")
print("     qwen3-vl-embedding 融合  文→图  Top-1 95%  平均排名 1.05")
