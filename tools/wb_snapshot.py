"""Снимок исходных данных планировщика WB (только чтение БД) и сравнение режимов жадный / CP-SAT на одном снимке.

    python -m tools.wb_snapshot dump snapshot.json            # на сервере с БД
    python -m tools.wb_snapshot compare snapshot.json         # где угодно, где работает ortools: жадный и CP-SAT на тех же данных
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date

from gluing.wb.loader import context_for, load_cards
from gluing.wb.metrics import quality
from gluing.wb.planner import build_wb
from gluing.wb.profile import load_profile
from plan_wb import CABINETS


def dump(path: str) -> None:
    import psycopg
    from plan_ozon import database_url
    from planner.reader import read_rolls
    from planner.wb_reader import read_imt_members, read_wb_rows
    out: dict = {"as_of": date.today().isoformat(), "accounts": {}}
    with psycopg.connect(database_url()) as conn:
        out["rolls"] = read_rolls(conn)
        for acc, prof in CABINETS.items():
            p = load_profile(prof)
            rows = read_wb_rows(conn, acc, out["as_of"], p["stock"]["wb_mode"], grace_days=p["stock"].get("grace_days", 0))
            out["accounts"][acc] = {"rows": rows, "imt": {str(k): sorted(v) for k, v in read_imt_members(conn, acc).items()}}
        conn.rollback()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, default=str)
    print("снимок записан:", path, {a: len(v["rows"]) for a, v in out["accounts"].items()})


def compare(path: str) -> None:
    snap = json.load(open(path, encoding="utf-8"))
    total = {"greedy": {}, "cpsat": {}}
    for acc, prof_name in CABINETS.items():
        prof = load_profile(prof_name)
        ld = load_cards(snap["accounts"][acc]["rows"], snap["rolls"], prof)
        members = {int(k): set(v) for k, v in snap["accounts"][acc]["imt"].items()}
        print(f"\n=== {acc}: активных карточек {len(ld.cards)}")
        rows = {}
        for mode in ("greedy", "cpsat"):
            t = time.time()
            res = build_wb(ld.cards, ld.live, members, prof, context_for(prof), solver=mode)
            q = quality(res, ld.live, prof)
            q["секунд"] = round(time.time() - t, 1)
            rows[mode] = q
        for k in rows["greedy"]:
            g, c = rows["greedy"][k], rows["cpsat"][k]
            print(f"  {k:<32} жадный {g!s:>8}   CP-SAT {c!s:>8}")


if __name__ == "__main__":
    {"dump": lambda: dump(sys.argv[2]), "compare": lambda: compare(sys.argv[2])}[sys.argv[1]]()
