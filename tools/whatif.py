"""«Что если»: строит план в памяти по живым данным и сравнивает с живым состоянием Озона. В БД и на Озон ничего не пишет.

    python tools/whatif.py [--profile profiles/ozon.toml] [--json ops.json]

Печатает метрики плана, число операций по видам, заблокированные группы, предполётный отчёт и сколько групп/дизайнов
стало меньше или больше относительно сохранённого плана. Нужен, чтобы оценить правило до выкладки."""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from apply_ozon import check_plan  # noqa: E402
from applier.brands import load_brand_map  # noqa: E402
from applier.preflight import evaluate, render  # noqa: E402
from gluing.ozon.metrics import quality  # noqa: E402
from gluing.ozon.tree import build  # noqa: E402
from plan_ozon import database_url  # noqa: E402
from planner.plan import build_plan  # noqa: E402
from planner.reader import load_from_db  # noqa: E402
from planner.store import PgStore  # noqa: E402
from planner.twins import aspect_names, load_aspects, load_attrs, live_models  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=str(ROOT / "profiles" / "ozon.toml"))
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    with open(a.profile, "rb") as f:
        profile = tomllib.load(f)
    today = date.today()
    with psycopg.connect(database_url()) as conn:
        store = PgStore(conn)
        designs, unknown, pids, inputs = load_from_db(conn, profile, today.isoformat())
        prev = store.last("ozon")
        aspects, attrs, anames = load_aspects(conn), load_attrs(conn), aspect_names(conn)
        groups, log, state = build(designs, profile, prev.state if prev else None)
        plan = build_plan(groups, log, state, profile, run_id=f"{today.isoformat()}-whatif", inputs=inputs, platform_ids=pids,
                          prev_ids={g.key: g.group_id for g in prev.groups} if prev else {}, live_models=live_models(attrs),
                          attrs=attrs, aspects=aspects, aspect_names=anames)
        res = check_plan(conn, plan, load_brand_map(profile))
        status = "draft"
        prev_groups = len(prev.groups) if prev else 0
        prev_kinds = Counter(g.kind for g in prev.groups) if prev else Counter()
    print("=== метрики плана")
    for k, v in quality(groups).items():
        print(f"{k}: {v}")
    kinds = Counter(g.kind for g in plan.groups)
    print(f"групп в плане: {len(plan.groups)} (было {prev_groups}); по видам: {dict(kinds)} (было {dict(prev_kinds)})")
    print(f"жёстких нарушений: {len(plan.checks.hard_violations)}; близнецов: {len(plan.checks.twins)}; "
          f"дизайнов с правкой цвета: {len(plan.checks.colors)}")
    print("=== операции на Озоне по видам:", dict(Counter(o.op_type for o in res.ops)), "всего", len(res.ops))
    print(f"заблокировано групп: {len(res.blocked)}")
    for b in res.blocked[:15]:
        print("  ", b.group_id, "|", "; ".join(b.reasons)[:200])
    snaps = {k: v for k, v in plan.run.inputs.items() if k in ("ozon_stock_snapshot", "rolls_snapshot")}
    print("=== предполётный отчёт")
    print(render(evaluate(plan, res, status, snaps, today)))
    sized = Counter()
    for g in plan.groups:
        if " › " in g.branch and g.type == "Комплект постельного белья":
            sized[(g.account, g.brand)] += 1
    print("КПБ-групп с размерностью по брендам:", dict(sized))
    small = [(g.account, g.brand, g.branch, g.n_designs) for g in plan.groups if g.kind != "glue" and g.type == "Комплект постельного белья"]
    print(f"КПБ малых/одиночных групп: {len(small)}; примеры:", small[:12])
    if a.json:
        Path(a.json).write_text(json.dumps({"ops": [o.__dict__ for o in res.ops]}, ensure_ascii=False, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
