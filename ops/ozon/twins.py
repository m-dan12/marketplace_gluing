from ops import ENV_PATH
import csv, collections, json, sys
from ops.ozon import live_apply as A
env = A.read_env(ENV_PATH)
out = {}
for acct, sfx in (("proftex", ""), ("milky", "_MILKY"), ("timeless", "_TIMELESS")):
    oz = A.Ozon(env["OZON_CLIENT_ID" + sfx], env["OZON_API_KEY" + sfx])
    d = f"logs/20261009-190720-{acct}"
    res = list(csv.DictReader(open(d + "/results.csv", encoding="utf-8-sig")))
    bad = {r["offer_id"] for r in csv.DictReader(open(d + "/still_bad.csv", encoding="utf-8-sig"))}
    groups = {g for g in {r["to"] for r in res if r["offer_id"] in bad}}
    members = [r for r in res if r["to"] in groups]
    attrs = A.read_models(oz, [r["offer_id"] for r in members])
    full = {}
    for i in range(0, len(members), 100):
        b = [r["offer_id"] for r in members[i:i+100]]
        for p in oz.post("/v4/product/info/attributes", {"filter": {"offer_id": b, "visibility": "ALL"}, "limit": 100})["result"]:
            full[p["offer_id"]] = p
    # аспектные (вариативные) атрибуты по категориям
    aspects = {}; names = {}
    for p in full.values():
        k = (p["description_category_id"], p["type_id"])
        if k not in aspects:
            r = oz.post("/v1/description-category/attribute", {"description_category_id": k[0], "type_id": k[1], "language": "DEFAULT"})["result"]
            aspects[k] = [a["id"] for a in r if a.get("is_aspect")]; names.update({a["id"]: a["name"] for a in r})
    def sig(p):
        k = (p["description_category_id"], p["type_id"])
        return tuple((a, tuple(v["value"] for v in x["values"])) for a in aspects[k] for x in p["attributes"] if x["id"] == a)
    bysig = collections.defaultdict(list)
    for r in members:
        p = full[r["offer_id"]]; bysig[(r["to"], sig(p))].append(r["offer_id"])
    twin = nontwin = 0; ex = []
    for o in bad:
        p = full.get(o)
        if not p: continue
        g = next(r["to"] for r in members if r["offer_id"] == o)
        mates = [x for x in bysig[(g, sig(p))] if x != o]
        if mates: twin += 1; ex.append((o, mates[0], sig(p)))
        else: nontwin += 1
    asp_names = sorted({names[a] for k in aspects for a in aspects[k]})
    print(f"== {acct}: проблемных {len(bad)} | есть «близнец» в своей группе по вариативным атрибутам: {twin} | нет: {nontwin}")
    print("   вариативные атрибуты в категориях:", asp_names)
    for o, m, s in ex[:3]: print("   пример:", o, "≈", m, "|", [(names[a], v) for a, v in s][:4])
    out[acct] = ex[:200]
