from ops import ENV_PATH
import csv, json, os, sys, time, urllib.request
env = {}
for l in open(ENV_PATH, encoding="utf-8-sig"):
    if "=" in l and not l.startswith("#"):
        k, v = l.rstrip("\r\n").split("=", 1); env[k.strip()] = v.strip().strip('"')
H = {"Client-Id": env["OZON_CLIENT_ID"], "Api-Key": env["OZON_API_KEY"], "Content-Type": "application/json"}
def post(path, body):
    for a in range(4):
        try:
            r = urllib.request.Request("https://api-seller.ozon.ru" + path, json.dumps(body).encode(), H)
            return json.load(urllib.request.urlopen(r, timeout=60))
        except Exception as e:
            err = e; time.sleep(2 + a * 2)
    raise SystemExit(f"{path}: {type(err).__name__} {getattr(err,'code','')}")
items, last = [], ""
while True:
    r = post("/v3/product/list", {"filter": {"visibility": "ALL"}, "last_id": last, "limit": 1000})["result"]
    items += r["items"]; last = r["last_id"]
    if not r["items"] or len(items) >= r["total"]: break
print("товаров:", len(items), file=sys.stderr)
ids = [i["product_id"] for i in items]
info = []
for i in range(0, len(ids), 1000):
    info += post("/v3/product/info/list", {"product_id": ids[i:i+1000]})["items"]
tree = post("/v1/description-category/tree", {"language": "DEFAULT"})["result"]
tname = {}
def walk(nodes, cat=None):
    for n in nodes:
        if "type_id" in n: tname[(cat, n["type_id"])] = n["type_name"]
        walk(n.get("children", []), n.get("description_category_id", cat))
walk(tree)
model = {}
for i in range(0, len(ids), 1000):
    r = post("/v4/product/info/attributes", {"filter": {"product_id": ids[i:i+1000], "visibility": "ALL"}, "limit": 1000})
    for p in r["result"]:
        for a in p.get("attributes", []):
            if a["id"] == 9048 and a["values"]:
                model[p["id"]] = a["values"][0]["value"]
with open("C:/mg/out/ozon_proftex.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["account","offer_id","title","category","stock","orders28","model_name","product_id"])
    for p in info:
        st = sum(s.get("present", 0) for s in p.get("stocks", {}).get("stocks", []))
        t = tname.get((p.get("description_category_id"), p.get("type_id")), "?")
        w.writerow(["proftex", p["offer_id"], p["name"], t, st, 0, model.get(p["id"], ""), p["id"]])
print("строк:", len(info), "с моделью:", len(model), file=sys.stderr)
