import json, base64, math, os, urllib.request, urllib.error, time

KEY = open("bailian.key").read().strip()
EP = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"

def call(model, contents, fusion=None):
    params = {}
    if fusion is not None: params["enable_fusion"] = fusion
    body = {"model": model, "input": {"contents": contents}}
    if params: body["parameters"] = params
    r = urllib.request.Request(EP, data=json.dumps(body).encode(), method="POST")
    r.add_header("Authorization", "Bearer " + KEY); r.add_header("Content-Type", "application/json")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                d = json.loads(resp.read().decode())
            embs = d["output"]["embeddings"]
            out = []
            for e in embs:
                v = e.get("embedding")
                if v is None: return None, d
                out.append(v)
            return out, None
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            if e.code == 429: time.sleep(5*(attempt+1)); continue
            return None, {"http": e.code, "msg": msg}
        except Exception as e:
            time.sleep(3); last = str(e)
    return None, {"err": "retries exhausted"}

def cos(a, b):
    dot = sum(x*y for x, y in zip(a, b))
    na = math.sqrt(sum(x*x for x in a)); nb = math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0

# 载入 MinerU 结果，取有 caption 的图片
d = json.load(open("mineru_out/499cde81-c591-4bca-b372-1161ea1d8d07_content_list_v2.json"))
pairs = []
for pi, page in enumerate(d):
    for b in page:
        if b.get("type") in ("image", "chart"):
            caps = b["content"].get("image_caption") or []
            txt = " ".join(x.get("content","") for x in caps if isinstance(x, dict)).strip()
            p = b["content"].get("image_source", {}).get("path")
            if txt and p and os.path.exists("mineru_out/" + p):
                pairs.append((txt, p))
print(f"可用于测试的「图 + 图注」对: {len(pairs)} 对")
pairs = pairs[:20]
print(f"本次取前 {len(pairs)} 对\n")

def b64(p):
    return "data:image/jpeg;base64," + base64.b64encode(open("mineru_out/"+p,"rb").read()).decode()

MODELS = ["multimodal-embedding-v1", "tongyi-embedding-vision-plus", "qwen3-vl-embedding"]
for model in MODELS:
    # 文本向量（逐条）
    tvecs = []
    ok = True
    for txt, _ in pairs:
        v, err = call(model, [{"text": txt}])
        if v is None: print(f"  {model} 文本失败: {json.dumps(err,ensure_ascii=False)[:150]}"); ok=False; break
        tvecs.append(v[0])
    if not ok: continue
    ivecs = []
    for _, p in pairs:
        v, err = call(model, [{"image": b64(p)}])
        if v is None: print(f"  {model} 图片失败: {json.dumps(err,ensure_ascii=False)[:150]}"); ok=False; break
        ivecs.append(v[0])
    if not ok: continue

    dim = len(ivecs[0])
    hits1 = hits5 = 0
    ranks = []
    for i, iv in enumerate(ivecs):
        sims = sorted(range(len(tvecs)), key=lambda j: -cos(iv, tvecs[j]))
        r = sims.index(i) + 1
        ranks.append(r)
        if r == 1: hits1 += 1
        if r <= 5: hits5 += 1
    print(f"=== {model} (维度 {dim}) ===")
    print(f"  图→文 Top-1 命中: {hits1}/{len(pairs)} = {hits1/len(pairs)*100:.0f}%")
    print(f"  图→文 Top-5 命中: {hits5}/{len(pairs)} = {hits5/len(pairs)*100:.0f}%")
    print(f"  平均排名: {sum(ranks)/len(ranks):.2f}  (随机基线 {(len(pairs)+1)/2:.1f})")
    print()
