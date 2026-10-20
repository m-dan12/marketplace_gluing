"""Подробности к tools/whatif.py: одиночки, близнецы, правки цвета. Только чтение."""
from __future__ import annotations

import sys
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg  # noqa: E402
from gluing.ozon.tree import build  # noqa: E402
from plan_ozon import database_url  # noqa: E402
from planner.plan import build_plan  # noqa: E402
from planner.reader import load_from_db  # noqa: E402
from planner.store import PgStore  # noqa: E402
from planner.twins import aspect_names, load_aspects, load_attrs, live_models  # noqa: E402

profile = tomllib.load(open(ROOT / "profiles" / "ozon.toml", "rb"))
today = date.today().isoformat()
with psycopg.connect(database_url()) as conn:
    designs, unknown, pids, inputs = load_from_db(conn, profile, today)
    prev = PgStore(conn).last("ozon")
    aspects, attrs, anames = load_aspects(conn), load_attrs(conn), aspect_names(conn)
    groups, log, state = build(designs, profile, prev.state if prev else None)
    plan = build_plan(groups, log, state, profile, run_id="x", inputs=inputs, platform_ids=pids,
                      prev_ids={g.key: g.group_id for g in prev.groups} if prev else {}, live_models=live_models(attrs),
                      attrs=attrs, aspects=aspects, aspect_names=anames)
print("=== ОДИНОЧКИ (не склеить)")
for g in groups:
    if g.kind == "одиночка":
        for d in g.designs:
            print(g.account, g.brand, g.typ, "|", g.leaf, "|", d.design, [(s.offer_id, s.stock + s.stock_roll) for s in d.skus])
print("=== БЛИЗНЕЦЫ")
c = Counter()
for t in plan.checks.twins:
    c[(t["same_design"], bool(t["proposals"]))] += 1
print("(тот же дизайн?, есть правка?):", dict(c))
for t in plan.checks.twins[:8]:
    print(t["group_key"], "| цвет:", t["color"], "| дизайны:", t["designs"][:4], "| тот же дизайн:", t["same_design"])
    for p in t["proposals"][:2]:
        print("     ->", p["design"], p["offers"][:3], "=>", p["new_value"], f"({p['source']})")
print("=== ЕДИНЫЙ ЦВЕТ")
by = Counter(r["account"] for r in plan.checks.colors)
print("по кабинетам:", dict(by), "артикулов:", sum(len(r["offers"]) for r in plan.checks.colors))
for r in plan.checks.colors[:12]:
    print(r["account"], r["design"], "->", repr(r["value"]), "было:", sorted(set(r["offers"].values()), key=str))
