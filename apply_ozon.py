"""Применяющий скрипт Озона. Сейчас умеет только dry-run: разница с живым состоянием и проверки, на площадку ничего не пишет.

    python apply_ozon.py                      # последний план, живое состояние из gluing.sku_attr
    python apply_ozon.py --run 2026-10-12-ozon --json ops.json
    python apply_ozon.py --refresh --env C:\Proftex-Server\marketplace_db_sync\.env   # сначала перечитать атрибуты с Озона (чтение)
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import psycopg

from applier.ops import dry_run
from plan_ozon import database_url
from planner.store import PgStore
from planner.twins import load_aspects, load_attrs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="", help="run_id плана; по умолчанию последний план Озона")
    ap.add_argument("--refresh", action="store_true", help="перечитать атрибуты с Озона (только чтение) перед проверкой")
    ap.add_argument("--env", default="", help=".env проекта db_sync с ключами (для --refresh)")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    with psycopg.connect(database_url()) as conn:
        if a.refresh:
            from planner.ozon_attrs import run as refresh
            if not a.env:
                raise SystemExit("для --refresh нужен --env")
            print("обновлено:", refresh(conn, a.env, None, False))
        store = PgStore(conn)
        plan = store.load(a.run) if a.run else store.last("ozon")
        if plan is None:
            raise SystemExit("план не найден")
        res = dry_run(plan, load_attrs(conn), load_aspects(conn))
        conn.rollback()
    print("режим: dry-run, на Озон ничего не отправляется")
    for k, v in res.summary().items():
        print(f"{k}: {v}")
    for b in res.blocked[:15]:
        print("  ЗАБЛОКИРОВАНА", b.group_id, "|", "; ".join(b.reasons)[:200])
    for w in res.warnings[:10]:
        print("  предупреждение:", w[:200])
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"summary": res.summary(), "ops": [asdict(o) for o in res.ops],
                       "blocked": [asdict(b) for b in res.blocked], "warnings": res.warnings}, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
