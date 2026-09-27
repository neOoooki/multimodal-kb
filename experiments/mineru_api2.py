import json, os, subprocess, time, urllib.request, urllib.error

TOKEN = os.environ.get("MINERU_TOKEN") or open(
    os.path.expanduser("~/.secrets/mineru.key")).read().strip()
BASE = "https://mineru.net/api/v4"
PDF = os.environ.get("PDF", "book.pdf")

def api(method, path, payload=None, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Authorization", "Bearer " + TOKEN)
    if data: r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode()[:300]}

st = api("POST", "/file-urls/batch", {
    "files": [{"name": "电子工艺实训基础-1-100.pdf", "data_id": "dianzi-100"}],
    "model_version": "vlm",
})
if st.get("code") != 0:
    raise SystemExit(f"申请上传失败: {json.dumps(st, ensure_ascii=False)[:300]}")
batch_id = st["data"]["batch_id"]; url = st["data"]["file_urls"][0]
print("batch_id:", batch_id)

# 用 curl 上传，显式清空 Content-Type（urllib 会自动加，导致 403）
cp = subprocess.run(["curl", "-s", "-X", "PUT", "-H", "Content-Type:", "--data-binary", f"@{PDF}",
                     "-o", "/dev/null", "-w", "%{http_code}", url], capture_output=True, text=True)
print("上传 HTTP:", cp.stdout.strip())
if cp.stdout.strip() != "200":
    raise SystemExit("上传失败")

for i in range(150):
    time.sleep(10)
    res = api("GET", f"/extract-results/batch/{batch_id}")
    items = (res.get("data") or {}).get("extract_result") or []
    if not items:
        print(f"  [{i*10}s] {json.dumps(res, ensure_ascii=False)[:150]}"); continue
    it = items[0]; state = it.get("state")
    print(f"  [{i*10}s] state={state} err={it.get('err_msg','')}")
    if state == "done":
        open("/tmp/_mu_zip.txt","w").write(it["full_zip_url"])
        print("✅ 完成"); break
    if state == "failed":
        print("❌", it.get("err_msg")); break
