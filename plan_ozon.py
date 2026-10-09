"""Ежедневный запуск планировщика Озона (теневой режим): читает БД, строит план, сохраняет черновик в схему gluing.

    python plan_ozon.py                      # строит и сохраняет план
    python plan_ozon.py --dry                # только считает и печатает метрики, в БД ничего не пишет
    python plan_ozon.py --json plan.json --xlsx plan.xlsx

Строка подключения: DATABASE_URL_GLUING из окружения или из .env рядом со скриптом.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import tomllib
from datetime import date
from pathlib import Path

import psycopg

from gluing.ozon.metrics import churn, quality
from gluing.ozon.report import write_report
from gluing.ozon.tree import build
from planner.plan import build_plan
from planner.reader import load_from_db
from planner.store import PgStore

ROOT = Path(__file__).resolve().parent


def database_url() -> str:
    url = os.environ.get("DATABASE_URL_GLUING")
    env = ROOT / ".env"
    if not url and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL_GLUING="):
                url = line.split("=", 1)[1].strip()
    if not url:
        raise SystemExit("нет DATABASE_URL_GLUING (окружение или .env)")
    return url


def engine_version() -> str:
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        return f"git:{h}" if h else "dev"
    except OSError:
        return "dev"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=str(ROOT / "profiles" / "ozon.toml"))
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--dry", action="store_true", help="не писать в БД")
    ap.add_argument("--json", default="")
    ap.add_argument("--xlsx", default="")
    a = ap.parse_args()
    with open(a.profile, "rb") as f:
        profile = tomllib.load(f)

    with psycopg.connect(database_url()) as conn:
        store = PgStore(conn)
        designs, unknown, pids, inputs = load_from_db(conn, profile, a.as_of)
        prev = store.last("ozon")                                  # теневой режим: вчерашний план любого статуса
        prev_state = prev.state if prev else None
        prev_ids = {g.key: g.group_id for g in prev.groups} if prev else {}
        groups, log, state = build(designs, profile, prev_state)
        run_id = f"{a.as_of}-ozon"
        n = 1
        while store.status(run_id) is not None:                    # повторный запуск в тот же день
            n += 1
            run_id = f"{a.as_of}-ozon-{n}"
        plan = build_plan(groups, log, state, profile, run_id=run_id, engine_version=engine_version(),
                          inputs=inputs, platform_ids=pids, prev_ids=prev_ids)
        q = quality(groups)
        for k, v in q.items():
            print(f"{k}: {v}")
        if prev_state:
            for k, v in churn(prev_state, state).items():
                print(f"churn.{k}: {v}")
        print(f"неизвестных брендов: {len(unknown)}; нарушений жёстких правил: {len(plan.checks.hard_violations)}")
        if a.json:
            Path(a.json).write_text(plan.dumps(), encoding="utf-8")
        if a.xlsx:
            write_report(a.xlsx, groups, designs, log, q, churn(prev_state, state) if prev_state else None, unknown)
        if plan.checks.hard_violations:
            raise SystemExit("жёсткие нарушения, план не сохранён: " + "; ".join(plan.checks.hard_violations[:5]))
        if a.dry:
            print("--dry: в БД не пишу")
            return
        store.save_draft(plan)
        print("сохранён план", run_id)


if __name__ == "__main__":
    main()
