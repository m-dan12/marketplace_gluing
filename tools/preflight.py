"""Предполётный отчёт по плану Озона (только чтение БД; на Озон ничего не пишет).

    python -m tools.preflight                    # последний план
    python -m tools.preflight --run 2026-10-20-ozon --json report.json --examples 5

Проверяет статус и свежесть, жёсткие нарушения, близнецов, заблокированные группы, согласованность операций и выводит вердикт."""
from __future__ import annotations

import argparse
import json
import tomllib
from collections import Counter
from datetime import date

import psycopg

from applier.brands import load_brand_map
from applier.preflight import evaluate, render
from apply_ozon import check_plan
from plan_ozon import ROOT, database_url
from planner.store import PgStore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="")
    ap.add_argument("--json", default="")
    ap.add_argument("--examples", type=int, default=3, help="сколько примеров каждого вида операций показать")
    a = ap.parse_args()
    with open(ROOT / "profiles" / "ozon.toml", "rb") as f:
        profile = tomllib.load(f)
    with psycopg.connect(database_url()) as conn:
        store = PgStore(conn)
        plan = store.load(a.run) if a.run else store.last("ozon")
        if plan is None:
            raise SystemExit("план не найден")
        status = store.status(plan.run.run_id)
        res = check_plan(conn, plan, load_brand_map(profile))
    snaps = {k: v for k, v in plan.run.inputs.items() if k in ("ozon_stock_snapshot", "rolls_snapshot")}
    rep = evaluate(plan, res, status, snaps, date.today())
    print(render(rep))
    if a.examples:
        print("\nПримеры операций:")
        seen: Counter = Counter()
        for o in res.ops:
            if seen[o.op_type] < a.examples:
                seen[o.op_type] += 1
                print(f"  {o.op_type} {o.account} {o.offer_id}: «{o.from_value}» -> «{o.to_value}» (группа {o.group_id})")
        if res.blocked:
            print("\nЗаблокированные группы:")
            for b in res.blocked[:10]:
                print(f"  {b.group_id}: {'; '.join(b.reasons)[:200]}")
        if res.warnings:
            print("\nПредупреждения:")
            for w in res.warnings[:10]:
                print("  ", w[:220])
        if plan.checks.twins:
            print("\nБлизнецы (первые):")
            for t in plan.checks.twins[:5]:
                print("  ", t["group_id"], t["designs"], t["color"], t["proposals"][:1])
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"run_id": rep.run_id, "go": rep.go, "items": [i.__dict__ for i in rep.items], "metrics": rep.metrics},
                      f, ensure_ascii=False, indent=1)
    return 0 if rep.go else 2


if __name__ == "__main__":
    raise SystemExit(main())
