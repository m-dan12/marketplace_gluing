"""Сравнение раскладки частей: жадная эвристика против CP-SAT на живых данных (только чтение).  python -m tools.compare_parts"""
import statistics as st
import sys
import time
import tomllib

import psycopg

from gluing.ozon.metrics import churn, quality
from gluing.ozon.tree import build
from plan_ozon import database_url
from planner.reader import load_from_db
from planner.store import PgStore

prof = tomllib.load(open("profiles/ozon.toml", "rb"))
with psycopg.connect(database_url()) as c:
    ds, unk, pids, inp = load_from_db(c, prof, sys.argv[1] if len(sys.argv) > 1 else "2026-10-19")
    prev = PgStore(c).last("ozon")
    c.rollback()
res = {}
for name, parts in (("жадная", "greedy"), ("CP-SAT", "cpsat")):
    p = {**prof, "solver": {**prof["solver"], "parts": parts}}
    t = time.time()
    groups, log, state = build(ds, p, prev.state)
    dt = time.time() - t
    q = quality(groups)
    ch = churn(prev.state, state)
    # баланс силы и состав внутри веток, у которых несколько частей
    by = {}
    for g in groups:
        if g.kind == "склейка":
            by.setdefault((g.account, g.brand, g.typ, g.leaf), []).append(g)
    multi = {k: v for k, v in by.items() if len(v) > 1}
    spread = [max(sum(d.power for d in g.designs) for g in v) / max(1.0, min(sum(d.power for d in g.designs) for g in v)) for v in multi.values()]
    size_gap = [max(len(g.designs) for g in v) - min(len(g.designs) for g in v) for v in multi.values()]
    res[name] = (dt, q, ch, len(multi), st.mean(spread) if spread else 0, max(spread) if spread else 0, st.mean(size_gap) if size_gap else 0)
print(f"веток с несколькими частями: {res['жадная'][3]}")
rows = [("время расчёта, с", lambda r: f"{r[0]:.1f}"),
        ("склеек", lambda r: r[1]["склеек"]),
        ("доля склеек с хитом и неликвидом", lambda r: r[1]["доля склеек с хитом и неликвидом"]),
        ("разброс силы склеек (CV)", lambda r: r[1]["разброс силы склеек (CV)"]),
        ("сила сильной/слабой части, среднее", lambda r: f"{r[4]:.2f}"),
        ("сила сильной/слабой части, максимум", lambda r: f"{r[5]:.2f}"),
        ("разница размеров частей, среднее", lambda r: f"{r[6]:.2f}"),
        ("сменили склейку за день", lambda r: f"{r[2]['сменили склейку']} ({r[2]['доля']:.1%})")]
for label, f in rows:
    print(f"{label:42s} | {f(res['жадная'])} | {f(res['CP-SAT'])}")
