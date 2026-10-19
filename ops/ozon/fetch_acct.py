import csv, sys, collections, datetime as dt, os
acct = sys.argv[1]
from ops.ozon.ozapi import post
items, last = [], ""
while True:
    r = post("/v3/product/list", {"filter": {"visibility": "ALL"}, "last_id": last, "limit": 1000})["result"]
    items += r["items"]; last = r["last_id"]
    if not r["items"] or len(items) >= r["total"]: break
ids = [i["product_id"] for i in items]; info = []
for i in range(0, len(ids), 1000): info += post("/v3/product/info/list", {"product_id": ids[i:i+1000]})["items"]
tname = {}
def walk(nodes, cat=None):
    for n in nodes:
        if "type_id" in n: tname[(cat, n["type_id"])] = n["type_name"]
        walk(n.get("children", []), n.get("description_category_id", cat))
walk(post("/v1/description-category/tree", {"language": "DEFAULT"})["result"])
model = {}
for i in range(0, len(ids), 1000):
    for p in post("/v4/product/info/attributes", {"filter": {"product_id": ids[i:i+1000], "visibility": "ALL"}, "limit": 1000})["result"]:
        for a in p.get("attributes", []):
            if a["id"] == 9048 and a["values"]: model[p["id"]] = a["values"][0]["value"]
to = dt.datetime.utcnow(); since = to - dt.timedelta(days=28); f = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
cnt = collections.Counter()
for path, key in (("/v3/posting/fbs/list", "postings"), ("/v2/posting/fbo/list", None)):
    off = 0
    while True:
        r = post(path, {"dir": "ASC", "filter": {"since": f(since), "to": f(to)}, "limit": 1000, "offset": off})["result"]
        ps = r[key] if key else r
        for p in ps:
            if p.get("status") != "cancelled":
                for pr in p.get("products", []): cnt[pr["offer_id"]] += pr.get("quantity", 1)
        if len(ps) < 1000: break
        off += 1000
with open(f"out/ozon_{acct}.csv", "w", encoding="utf-8", newline="") as fh:
    w = csv.writer(fh); w.writerow(["account","offer_id","title","category","stock","orders28","model_name","product_id"])
    for p in info:
        st = sum(s.get("present", 0) for s in p.get("stocks", {}).get("stocks", []))
        w.writerow([acct, p["offer_id"], p["name"], tname.get((p.get("description_category_id"), p.get("type_id")), "?"), st, cnt.get(p["offer_id"], 0), model.get(p["id"], ""), p["id"]])
print(acct, "товаров", len(info), "с моделью", len(model), "заказов", sum(cnt.values()))
