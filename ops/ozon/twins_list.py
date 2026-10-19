from ops import ENV_PATH
import csv, collections
from ops.ozon import live_apply as A
env = A.read_env(ENV_PATH)
rows_out = []
for acct, sfx in (("proftex", ""), ("milky", "_MILKY"), ("timeless", "_TIMELESS")):
    oz = A.Ozon(env["OZON_CLIENT_ID" + sfx], env["OZON_API_KEY" + sfx])
    d = f"logs/20261009-190720-{acct}"
    res = list(csv.DictReader(open(d + "/results.csv", encoding="utf-8-sig")))
    bad = [r["offer_id"] for r in csv.DictReader(open(d + "/still_bad.csv", encoding="utf-8-sig"))]
    grp = {r["offer_id"]: r["to"] for r in res}
    members = [r["offer_id"] for r in res if r["to"] in {grp[o] for o in bad}]
    full = {}
    for i in range(0, len(members), 100):
        for p in oz.post("/v4/product/info/attributes", {"filter": {"offer_id": members[i:i+100], "visibility": "ALL"}, "limit": 100})["result"]:
            full[p["offer_id"]] = p
    asp, nm = {}, {}
    for p in full.values():
        k = (p["description_category_id"], p["type_id"])
        if k not in asp:
            r = oz.post("/v1/description-category/attribute", {"description_category_id": k[0], "type_id": k[1], "language": "DEFAULT"})["result"]
            asp[k] = [a["id"] for a in r if a.get("is_aspect")]; nm.update({a["id"]: a["name"] for a in r})
    sig = lambda p: tuple((a, tuple(v["value"] for v in x["values"])) for a in asp[(p["description_category_id"], p["type_id"])] for x in p["attributes"] if x["id"] == a)
    by = collections.defaultdict(list)
    for o in members: by[(grp[o], sig(full[o]))].append(o)
    for o in bad:
        p = full[o]; mates = [x for x in by[(grp[o], sig(p))] if x != o]
        rows_out.append({"кабинет": acct, "группа": grp[o], "проблемный_артикул": o, "название": p["name"][:70],
                         "двойник_в_группе": mates[0] if mates else "", "всего_двойников": len(mates),
                         "совпавшие_характеристики": "; ".join(f"{nm[a]}={'/'.join(v)}" for a, v in sig(p))})
f = open("twins_list.csv", "w", encoding="utf-8-sig", newline="")
w = csv.DictWriter(f, fieldnames=list(rows_out[0])); w.writeheader(); w.writerows(rows_out); f.close()
print(len(rows_out), "строк;", sum(1 for r in rows_out if r["двойник_в_группе"]), "с двойником")
for r in rows_out[:4]: print(r["проблемный_артикул"], "≈", r["двойник_в_группе"], "|", r["совпавшие_характеристики"][:110])
