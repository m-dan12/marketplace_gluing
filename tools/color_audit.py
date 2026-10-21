"""Единство «Названия цвета» внутри дизайна по последнему плану Озона и живым атрибутам (только чтение).

Считает дизайны плана, у артикулов которых на Озоне разные значения цвета, и разбирает причины: переименование близнецов того же
дизайна (так задумано), списки цветов вместо названия (дизайн не трогаем), прочее."""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg  # noqa: E402
from plan_ozon import database_url  # noqa: E402
from planner.plan import is_color_name  # noqa: E402
from planner.store import PgStore  # noqa: E402
from planner.twins import COLOR_NAME_ATTR, load_attrs  # noqa: E402

with psycopg.connect(database_url()) as conn:
    plan = PgStore(conn).last("ozon")
    attrs = load_attrs(conn)
twin_offers = {(g["group_id"], o) for g in plan.checks.twins if g.get("same_design") for o in g["offers"]}
twin_o = {o for _, o in twin_offers}
by = defaultdict(dict)
for g in plan.groups:
    for d in g.designs:
        for m in d.members:
            v = ((attrs.get((g.account, m.offer_id)) or {}).get("attrs", {}).get(str(COLOR_NAME_ATTR)) or [""])[0]
            by[(g.account, d.design)][m.offer_id] = v
total, multi, why = len(by), 0, Counter()
examples = defaultdict(list)
for key, vals in by.items():
    if len(set(vals.values())) <= 1:
        continue
    multi += 1
    names = {v for v in vals.values() if is_color_name(v)}
    if not names:
        k = "нет ни одного названия (только списки цветов/пусто): не трогаем"
    elif any(o in twin_o for o in vals):
        k = "артикулы-близнецы одного дизайна (разные значения нужны Озону)"
    elif "" in vals.values():
        k = "часть значений пустая"
    else:
        k = "прочее"
    why[k] += 1
    if len(examples[k]) < 4:
        examples[k].append((key, sorted(set(vals.values()))))
print(f"план {plan.run.run_id}: дизайнов {total}, с разным цветом у артикулов {multi} ({multi * 100 // max(total, 1)}%)")
for k, n in why.most_common():
    print(f"  {n}: {k}")
    for e in examples[k]:
        print("      ", e)
