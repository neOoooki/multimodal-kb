import json, base64, os, urllib.request, urllib.error, time
K = open("bailian.key").read().strip()
RERANK = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

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

docs=[{"image":b64(p)} for _,p in pairs]

def rerank(query, documents, top_n=None):
    body={"model":"qwen3-vl-rerank","input":{"query":query,"documents":documents}}
    if top_n: body["parameters"]={"top_n":top_n}
    for a in range(4):
        r=urllib.request.Request(RERANK,data=json.dumps(body).encode(),method="POST")
        r.add_header("Authorization","Bearer "+K); r.add_header("Content-Type","application/json")
        try:
            with urllib.request.urlopen(r,timeout=120) as resp:
                return json.loads(resp.read().decode())["output"]["results"]
        except urllib.error.HTTPError as e:
            if e.code==429: time.sleep(5*(a+1)); continue
            return {"err":e.code,"msg":e.read().decode()[:150]}
    return {"err":"retries"}

h1=0; ranks=[]; fails=0
for i,(txt,_) in enumerate(pairs):
    res = rerank(txt, docs, top_n=len(docs))
    if isinstance(res, dict): fails+=1; continue
    order=[x["index"] for x in sorted(res, key=lambda x:-x["relevance_score"])]
    r=order.index(i)+1; ranks.append(r); h1+=(r==1)
n=len(pairs)-fails
print(f"=== qwen3-vl-rerank：图注文本 → 图片 重排序 ===")
print(f"  参与测试: {n}/{len(pairs)}  (失败 {fails})")
print(f"  Top-1 命中: {h1}/{n} = {h1/n*100:.0f}%")
print(f"  平均排名: {sum(ranks)/len(ranks):.2f} (随机 {(n+1)/2:.1f})")
print()
print("对照（同样的 20 组）：")
print("  multimodal-embedding-v1 文→图 Top-1 = 40%  平均排名 4.60")
print("  qwen3-vl-embedding      文→图 Top-1 = 80%  平均排名 1.40")
print("  qwen3-vl-embedding 融合 文→图 Top-1 = 95%  平均排名 1.05")
