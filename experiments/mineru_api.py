import json, os, time, urllib.request, urllib.error, io, zipfile

TOKEN = os.environ.get("MINERU_TOKEN") or open(
    os.path.expanduser("~/.secrets/mineru.key")).read().strip()
BASE = "https://mineru.net/api/v4"

def req(method, path, payload=None, raw=None, ctype="application/json", timeout=120):
    data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", ctype)
    if "mineru.net" in BASE:
        r.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try: return e.code, json.loads(body or "{}")
        except Exception: return e.code, {"raw": body[:400]}

PDF = os.environ.get("PDF", "book.pdf")

# 1) 申请上传链接
st, r = req("POST", "/file-urls/batch", {
    "files": [{"name": "电子工艺实训基础-1-100.pdf", "data_id": "dianzi-100"}],
    "model_version": "vlm",
})
print("申请上传:", st, json.dumps(r, ensure_ascii=False)[:250])
if r.get("code") != 0:
    raise SystemExit("失败")
batch_id = r["data"]["batch_id"]
upload_url = r["data"]["file_urls"][0]
print("batch_id:", batch_id)

# 2) PUT 上传（不带 Content-Type）
with open(PDF, "rb") as fh:
    data = fh.read()
r2 = urllib.request.Request(upload_url, data=data, method="PUT")
with urllib.request.urlopen(r2, timeout=600) as resp:
    print("上传文件: HTTP", resp.status, f"({len(data)/1024/1024:.2f} MB)")

open("/tmp/_mu_batch.txt", "w").write(batch_id)

# 3) 轮询结果
for i in range(120):
    time.sleep(10)
    st, res = req("GET", f"/extract-results/batch/{batch_id}")
    if res.get("code") != 0:
        print("轮询异常:", json.dumps(res, ensure_ascii=False)[:200]); continue
    items = res["data"].get("extract_result") or []
    if not items: continue
    it = items[0]
    state = it.get("state")
    print(f"  [{i*10}s] state={state} err={it.get('err_msg','')}")
    if state == "done":
        url = it.get("full_zip_url")
        print("结果包:", url[:110])
        open("/tmp/_mu_zip.txt", "w").write(url)
        break
    if state == "failed":
        print("解析失败:", it.get("err_msg")); break
