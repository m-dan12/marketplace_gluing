from ops import ENV_PATH
import csv, json, re, time, urllib.request, urllib.parse, collections, sys
env = {}
for l in open(ENV_PATH, encoding="utf-8-sig"):
    if "=" in l and not l.startswith("#"):
        k, v = l.rstrip("\r\n").split("=", 1); env[k.strip()] = v.strip().strip('"')
H = {"Authorization": env["SELSUP_API_TOKEN"]}
def get(path, params):
    q = urllib.parse.urlencode(params, doseq=True)
    for a in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request("https://api.selsup.ru" + path + "?" + q, headers=H), timeout=90))
        except Exception as e:
            err = e; time.sleep(2 + a * 2)
    raise SystemExit(f"{path}: {type(err).__name__} {getattr(err,'code','')}")
rows = [r for r in get("/api/wms/stock/all", {"warehouseId": 10001}) if r.get("quantity")]
pid = lambda r: ((r.get("sku") or {}).get("product") or {}).get("id") or r.get("skuId")
ids = list({pid(r) for r in rows if pid(r)})
info = {}
for i in range(0, len(ids), 200):
    for x in get("/api/product/find", {"ids": ids[i:i+200], "limit": 200}).get("rows", []): info[x["id"]] = x
m = collections.Counter()
for r in rows:
    p = (r.get("sku") or {}).get("product") or {}
    art = p.get("anyArticle") or (((info.get(pid(r)) or {}).get("view") or {}).get("model") or {}).get("article")
    if art and re.fullmatch(r"PT\d+", art): m[art] += float(r["quantity"])
with open("out/rolls.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["article", "meters"]); [w.writerow([a, int(q)]) for a, q in sorted(m.items())]
print("строк на FBS:", len(rows), "рулонов PT:", len(m), "метров:", int(sum(m.values())), file=sys.stderr)
