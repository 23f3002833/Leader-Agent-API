import os, json, httpx

ROOT = os.environ["ROOT_URL"]

def fetch(url):
    r = httpx.get(url, timeout=30)
    print("STATUS", r.status_code, "| bytes:", len(r.content))
    return r

links = fetch(ROOT).json()["links"]

print("\n===== RATES =====")
print(fetch(links["rates"]).text[:1500])

print("\n===== ORDERS page 1 =====")
d = fetch(links["orders"]).json()
if isinstance(d, dict):
    print("top-level keys:", list(d.keys()))
print(json.dumps(d, indent=1)[:2500])

print("\n===== EXPORT =====")
r = fetch(links["export"])
print(r.headers.get("content-type"))
print(r.text[:1500])
