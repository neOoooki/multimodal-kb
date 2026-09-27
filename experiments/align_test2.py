import json, base64, math, os, urllib.request, urllib.error, time

KEY = open("bailian.key").read().strip()
EP = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"

def call(model, contents, fusion=None, dim=None):
    p = {}
    if fusion is not None: p["enable_fusion"] = fusion
    if dim is not None: p["dimension"] = dim
    body = {"model": model, "input": {"contents": contents}}
    if p: body["parameters"] = p
    for attempt in range(4):
        r = urllib.request.Request(EP, data=json.dumps(body).encode(), method="POST")
        r.add_header("Authorization", "Bearer " + KEY); r.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(r, timeout=120) as resp:
                d = json.loads(resp.read().decode())
            return [e.get("embedding") for e in d["output"]["embeddings"]], None
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            if e.code == 429: time.sleep(5*(attempt+1)); continue
            return None, {"http": e.code, "msg": msg}
        except Exception as e:
            time.sleep(3)
    return None, {"err": "retries"}

def cos(a,b):
    dot=sum(x*y for x,y in zip(a,b)); na=math.sqrt(sum(x*x for x in a)); nb=math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else 0.0

d = json.load(open("mineru_out/499cde81-c591-4bca-b372-1161ea1d8d07_content_list_v2.json"))
pairs=[]
for pi,page in enumerate(d):
    for b in page:
        if b.get("type") in ("image","chart"):
            caps=b["content"].get("image_caption") or []
            txt=" ".join(x.get("content","") for x in caps if isinstance(x,dict)).strip()
            p=b["content"].get("image_source",{}).get("path")
            if txt and p and os.path.exists("mineru_out/"+p): pairs.append((txt,p))
pairs=pairs[:20]
b64=lambda p:"data:image/jpeg;base64,"+base64.b64encode(open("mineru_out/"+p,"rb").read()).decode()
print(f"测试对: {len(pairs)}\n")

for model in ["multimodal-embedding-v1","qwen3-vl-embedding"]:
    tvecs=[]; ivecs=[]; bad=False
    for txt,_ in pairs:
        v,e=call(model,[{"text":txt}])
        if v is None: print(model,"文本失败",str(e)[:120]); bad=True; break
        tvecs.append(v[0])
    if bad: continue
    for _,p in pairs:
        v,e=call(model,[{"image":b64(p)}])
        if v is None: print(model,"图片失败",str(e)[:120]); bad=True; break
        ivecs.append(v[0])
    if bad: continue
    # 文→图
    h1=0; ranks=[]
    for i,tv in enumerate(tvecs):
        order=sorted(range(len(ivecs)), key=lambda j:-cos(tv,ivecs[j]))
        r=order.index(i)+1; ranks.append(r); h1+= (r==1)
    print(f"=== {model} 文→图 ===")
    print(f"  Top-1: {h1}/{len(pairs)} = {h1/len(pairs)*100:.0f}% | 平均排名 {sum(ranks)/len(ranks):.2f} (随机 {(len(pairs)+1)/2:.1f})")

# 融合向量：每个 (图注+图) 生成一个融合向量，再用图注文本去检索它
print("\n=== 融合向量路线 (qwen3-vl-embedding, enable_fusion=true) ===")
fvecs=[]; qvecs=[]; bad=False
for txt,p in pairs:
    v,e=call("qwen3-vl-embedding",[{"text":txt,"image":b64(p)}],fusion=True)
    if v is None: print("融合失败", str(e)[:150]); bad=True; break
    fvecs.append(v[0])
if not bad:
    for txt,_ in pairs:
        v,e=call("qwen3-vl-embedding",[{"text":txt}])
        if v is None: bad=True; break
        qvecs.append(v[0])
if not bad:
    h1=0; ranks=[]
    for i,qv in enumerate(qvecs):
        order=sorted(range(len(fvecs)), key=lambda j:-cos(qv,fvecs[j]))
        r=order.index(i)+1; ranks.append(r); h1+=(r==1)
    print(f"  文 → 融合向量 Top-1: {h1}/{len(pairs)} = {h1/len(pairs)*100:.0f}% | 平均排名 {sum(ranks)/len(ranks):.2f}")
