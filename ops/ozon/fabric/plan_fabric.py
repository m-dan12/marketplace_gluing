import csv, tomllib, collections, sys
from gluing.ozon.loader import load_designs
from gluing.ozon.tree import build
from planner.ids import assign_ids
acct = sys.argv[1]
prof = tomllib.load(open("profiles/ozon.toml","rb"))
src = f"out/ozon_{acct}.csv"
rows = {r["offer_id"]: r for r in csv.DictReader(open(src, encoding="utf-8"))}
print("категории:", collections.Counter(r["category"] for r in rows.values()).most_common(12))
designs, unknown = load_designs(src, prof)
designs = [d for d in designs if d.typ == "Ткань"]
print("неизв. префиксы (всего по кабинету):", len(unknown), "| дизайнов ткани:", len(designs), "| SKU:", sum(len(d.skus) for d in designs), "| бренды:", collections.Counter(d.brand for d in designs))
groups, log, state = build(designs, prof)
live = {o: r["model_name"] for o, r in rows.items()}
ids = assign_ids([(g.key, [s.offer_id for d in g.designs for s in d.skus]) for g in groups], None, live)
ops, same = [], 0
for g in groups:
    print(g.key, g.kind, "дизайнов", len(g.designs), "SKU", sum(len(d.skus) for d in g.designs), "->", ids[g.key])
    for d in g.designs:
        for s in d.skus:
            old = live.get(s.offer_id, "")
            if old == ids[g.key]: same += 1
            else: ops.append({"offer_id": s.offer_id, "product_id": rows[s.offer_id]["product_id"], "from": old, "to": ids[g.key], "group_key": g.key, "kind": g.kind})
if ops:
    with open(f"out/ops_fabric_{acct}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ops[0].keys()); w.writeheader(); w.writerows(ops)
print("без изменений:", same, "| операций:", len(ops), "| старых моделей затронуто:", len({o["from"] for o in ops}))
