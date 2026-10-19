import csv, tomllib, collections
from gluing.ozon.loader import skus_from_rows, load_rolls, _type_of
from gluing.ozon.features import sku_feature, pick_feature
from gluing.ozon.model import Design
from gluing.ozon.tree import build
from gluing.ozon.metrics import quality
from planner.ids import assign_ids
prof = tomllib.load(open("profiles/ozon.toml","rb")); brands = prof["brands"]
rows_l = list(csv.DictReader(open("out/ozon_proftex.csv", encoding="utf-8")))
rows = {r["offer_id"]: r for r in rows_l}; live = {o: r["model_name"] for o, r in rows.items()}
skus, accts = skus_from_rows(rows_l, load_rolls("out/rolls.csv"))
def build_all(whole_design):
    active = {(a, brands.get(s.prefix), _type_of(s.category), s.prefix + s.design_no) for s, a in zip(skus, accts) if s.stock + s.stock_roll >= 2}
    ds, unk = {}, []
    for s, a in zip(skus, accts):
        b = brands.get(s.prefix)
        if b is None: (unk.append(s.offer_id) if s.stock + s.stock_roll >= 2 else None); continue
        typ = _type_of(s.category); key = (a, b, typ, s.prefix + s.design_no)
        if (key not in active) if whole_design else (s.stock + s.stock_roll < 2): continue
        d = ds.get(key) or ds.setdefault(key, Design(account=a, brand=b, typ=typ, design=key[3], digits=s.design_no))
        d.skus.append(s); d.features[sku_feature(typ, s)] += 1
    out = list(ds.values()); [setattr(d, "feature", pick_feature(d)) for d in out]; return out, unk
for whole in (False, True):
    designs, unk = build_all(whole); groups, log, state = build(designs, prof)
    print(f"{'весь дизайн' if whole else 'по остатку'}: дизайнов {len(designs)}, SKU {sum(len(d.skus) for d in designs)}, групп {len(groups)}", collections.Counter(g.kind for g in groups), "неизв.:", len(unk))
    if whole: final = (designs, groups, unk)
designs, groups, unk = final
NO9048 = {"Пеленка для малышей", "Карнавальная одежда"}
ids = assign_ids([(g.key, [s.offer_id for d in g.designs for s in d.skus]) for g in groups], None, live)
ops, same, skipped = [], 0, 0
for g in groups:
    for d in g.designs:
        for s in d.skus:
            if g.typ in NO9048: skipped += 1; continue
            old, new = live.get(s.offer_id, ""), ids[g.key]
            if old == new: same += 1
            else: ops.append({"offer_id": s.offer_id, "product_id": rows[s.offer_id]["product_id"], "type": g.typ, "brand": g.brand, "stock": s.stock + s.stock_roll, "from": old, "to": new, "group_key": g.key, "kind": g.kind})
with open("out/plan_full_ops.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=ops[0].keys()); w.writeheader(); w.writerows(ops)
print("SKU без изменений:", same, "| операций:", len(ops), "| пропущено (типы без 9048):", skipped)
bt = collections.defaultdict(lambda: [0, 0, 0]); 
for g in groups:
    t = bt[g.typ]; t[0] += 1; t[1] += len(g.designs); t[2] += sum(len(d.skus) for d in g.designs)
for t, (n, dz, sk) in sorted(bt.items(), key=lambda x: -x[1][2]): print(f"  {t}: групп {n}, дизайнов {dz}, SKU {sk}")
sz = [sum(len(d.skus) for d in g.designs) for g in groups]; print("SKU в группе: мин/медиана/макс", min(sz), sorted(sz)[len(sz)//2], max(sz))
print("модель g_ новых:", sum(1 for v in ids.values() if v.startswith("g_")), "унаследовано:", sum(1 for v in ids.values() if not v.startswith("g_")))
print({k: v for k, v in quality(groups).items() if k in ("склеек", "малых склеек", "одиночек (не склеить)", "чистота по признаку", "доля склеек с хитом и неликвидом")})
