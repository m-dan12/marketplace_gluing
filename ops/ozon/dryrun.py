import csv, tomllib, collections, json
from gluing.ozon.loader import load_designs
from gluing.ozon.tree import build
from planner.ids import assign_ids
prof = tomllib.load(open("profiles/ozon.toml","rb"))
designs, unknown = load_designs("out/ozon_proftex.csv", prof, "out/rolls.csv")
groups, log, state = build(designs, prof)
rows = {r["offer_id"]: r for r in csv.DictReader(open("out/ozon_proftex.csv", encoding="utf-8"))}
live = {o: r["model_name"] for o, r in rows.items()}
ids = assign_ids([(g.key, [s.offer_id for d in g.designs for s in d.skus]) for g in groups], None, live)
ops, same = [], 0
for g in groups:
    for d in g.designs:
        for s in d.skus:
            old, new = live.get(s.offer_id, ""), ids[g.key]
            if old == new: same += 1
            else: ops.append({"offer_id": s.offer_id, "product_id": rows[s.offer_id]["product_id"], "from": old, "to": new,
                              "group_key": g.key, "kind": g.kind})
with open("out/dryrun_ops.csv","w",encoding="utf-8",newline="") as f:
    w = csv.DictWriter(f, fieldnames=ops[0].keys()); w.writeheader(); w.writerows(ops)
json.dump({o["offer_id"]: o["from"] for o in ops}, open("out/rollback_old_models.json","w",encoding="utf-8"), ensure_ascii=False, indent=0)
inherit = sum(1 for k, v in ids.items() if not v.startswith("g_"))
print(f"групп: {len(groups)} (имя унаследовано: {inherit}, новое g_*: {len(groups)-inherit})")
print(f"SKU без изменений: {same}; операций (смена модели): {len(ops)} из {same+len(ops)}")
print("новые модели для SKU:", len({o['to'] for o in ops}), " старые модели, которые опустеют/изменятся:", len({o['from'] for o in ops}))
print("лишь пустое имя -> новое (SKU без модели):", sum(1 for o in ops if not o["from"]))
big = collections.Counter(o["group_key"] for o in ops).most_common(5); print(big)
