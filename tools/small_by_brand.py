"""Малые группы и одиночки плана в памяти по брендам и типам (только чтение). Для решений о слиянии брендов."""
from __future__ import annotations

import sys
import tomllib
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import psycopg  # noqa: E402
from gluing.ozon.tree import build  # noqa: E402
from plan_ozon import database_url  # noqa: E402
from planner.reader import load_from_db  # noqa: E402

profile = tomllib.load(open(sys.argv[1] if len(sys.argv) > 1 else ROOT / "profiles" / "ozon.toml", "rb"))
with psycopg.connect(database_url()) as conn:
    designs, unknown, pids, inputs = load_from_db(conn, profile, date.today().isoformat())
groups, log, state = build(designs, profile, None)
tot = defaultdict(lambda: [0, 0, 0, 0])
for g in groups:
    t = tot[(g.account, g.brand, g.typ)]
    t[0] += len(g.designs)
    t[1] += g.kind == "склейка"
    t[2] += g.kind == "малая"
    t[3] += g.kind in ("один дизайн", "одиночка")
print("кабинет | бренд | тип | дизайнов | склеек | малых | одиночек")
for k, v in sorted(tot.items()):
    if k[1] in ("Мечта", "Сказка Сатин", "Анна Мария PA", "Анна Мария AM"):
        print(" | ".join(k), "|", *v)
print("--- малые и одиночки этих брендов:")
for g in groups:
    if g.brand in ("Мечта", "Сказка Сатин", "Анна Мария PA", "Анна Мария AM") and g.kind != "склейка":
        print(g.brand, "|", g.typ, "|", g.leaf, "|", g.kind, "|", [d.design for d in g.designs])
