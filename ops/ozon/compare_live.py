import csv, tomllib, collections
from gluing.ozon.loader import load_designs
from gluing.ozon.tree import build
from gluing.ozon.metrics import quality
prof = tomllib.load(open("profiles/ozon.toml","rb"))
designs, unknown = load_designs("out/ozon_proftex.csv", prof, "out/rolls.csv")
groups, log, state = build(designs, prof)
print("дизайнов:", len(designs), "SKU в склейках:", sum(len(d.skus) for d in designs), "неизв. брендов:", len(unknown))
print(quality(groups))
print(collections.Counter(g.kind for g in groups))
live = {r["offer_id"]: r["model_name"] for r in csv.DictReader(open("out/ozon_proftex.csv", encoding="utf-8"))}
# SKU уровень: группа плана -> доминирующая живая модель
tot = ok = 0; gm = {}
for g in groups:
    c = collections.Counter(live.get(s.offer_id, "") for d in g.designs for s in d.skus)
    top, n = c.most_common(1)[0]; gm[g.key] = top
    ok += n; tot += sum(c.values())
print(f"SKU, чья живая модель = главной модели плановой группы: {ok}/{tot} = {ok/tot:.1%}")
# обратное: живая модель -> в какой одной плановой группе большинство
where = collections.defaultdict(collections.Counter)
for g in groups:
    for d in g.designs:
        for s in d.skus: where[live.get(s.offer_id,"")][g.key] += 1
ok2 = sum(c.most_common(1)[0][1] for c in where.values()); tot2 = sum(sum(c.values()) for c in where.values())
print(f"SKU, оставшиеся в той же группе, что и большинство их живой модели: {ok2}/{tot2} = {ok2/tot2:.1%}")
# дизайны целиком в одной живой модели
dok = sum(1 for d in designs if len({live.get(s.offer_id,"") for s in d.skus}) == 1)
print(f"дизайнов, у которых все SKU в одной живой модели: {dok}/{len(designs)} = {dok/len(designs):.1%}")
print("живых моделей (среди склеиваемых SKU):", len(where), "плановых групп:", len(groups))
inside = sum(sum(c.values()) for c in where.values() if len(c) == 1)
print(f"SKU, чья живая модель целиком лежит в одной плановой группе: {inside}/{tot2} = {inside/tot2:.1%}")
sizes = collections.Counter(sum(c.values()) for c in where.values()); import statistics
print("размер живой модели (SKU): медиана", statistics.median(sum(c.values()) for c in where.values()))
