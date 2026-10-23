"""Ежедневный запуск планировщика WB (теневой режим): читает БД, строит план по кабинетам, сохраняет черновик в схему gluing.

    python plan_wb.py                       # строит и сохраняет план
    python plan_wb.py --dry                 # только считает и печатает метрики, в БД ничего не пишет
    python plan_wb.py --accounts skazka     # один кабинет
Строка подключения: DATABASE_URL_GLUING из окружения или .env рядом со скриптом. На WB скрипт ничего не пишет.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date

import psycopg

from gluing.wb.loader import context_for, load_cards
from gluing.wb.planner import build_wb
from gluing.wb.profile import load_profile
from plan_ozon import database_url, engine_version
from planner.reader import read_rolls
from planner.store import PgStore
from planner.wb_plan import assemble_plan, build_wb_groups
from planner.wb_reader import read_imt_members, read_snapshots, read_wb_rows

CABINETS = {"skazka": "wb_proftex", "milky_garden": "wb_milky_garden", "timeless": "wb_timeless"}


def run_plan(as_of: str, dry: bool = False, accounts: list[str] | None = None, json_path: str = "") -> dict:
    accounts = accounts or list(CABINETS)
    profiles = {a: load_profile(CABINETS[a]) for a in accounts}
    with psycopg.connect(database_url()) as conn:
        store = PgStore(conn)
        rolls = read_rolls(conn)
        prev = store.last("wb")                                    # вчерашнее состояние гистерезиса размеров (теневой режим: любой статус)
        prev_sizes = (prev.state or {}).get("wb_sizes", {}) if prev else {}
        groups, loaded, inputs, sizes_state = [], {}, {"as_of": as_of}, {}
        for acc in accounts:
            prof = profiles[acc]
            rows = read_wb_rows(conn, acc, as_of, prof.get("stock", {}).get("wb_mode", "all_physical"),
                               grace_days=prof.get("stock", {}).get("grace_days", 0))
            ld = load_cards(rows, rolls, prof)
            res = build_wb(ld.cards, ld.live, read_imt_members(conn, acc), prof, context_for(prof), set(prev_sizes.get(acc, [])))
            sizes_state[acc] = sorted(res.separated or [])
            groups += build_wb_groups(acc, res, ld.live)
            loaded[acc] = ld
            kinds = Counter(s.kind for s in res.groups)
            print(f"{acc}: карточек в плане {len(ld.cards)}, склеек {kinds.get('glue', 0)}, малых {kinds.get('small_glue', 0)}, "
                  f"одиночек {kinds.get('single_design', 0)}, пропущено {ld.skipped}, перенос малых веток {dict(res.moved)}")
            inputs[f"{acc}_snapshots"] = read_snapshots(conn, acc)
        inputs.update(inputs[f"{accounts[0]}_snapshots"])           # общие даты снимков (остатки WB, Селсап) для проверки свежести
        run_id = f"{as_of}-wb"
        n = 1
        while store.status(run_id) is not None:
            n += 1
            run_id = f"{as_of}-wb-{n}"
        plan = assemble_plan(groups, profiles, loaded, run_id=run_id, engine_version=engine_version(), inputs=inputs,
                             profile_name="+".join(CABINETS[a] for a in accounts), state={"wb_sizes": sizes_state})
        print(f"жёстких нарушений: {len(plan.checks.hard_violations)}; предупреждений: {len(plan.checks.warnings)}")
        for w in plan.checks.warnings[:8]:
            print("  ", w[:200])
        if json_path:
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(plan.dumps())
        result = {"run_id": run_id, "saved": False, "groups": len(plan.groups), "hard_violations": list(plan.checks.hard_violations)}
        if plan.checks.hard_violations:
            raise SystemExit("жёсткие нарушения, план не сохранён: " + "; ".join(plan.checks.hard_violations[:5]))
        if dry:
            print("--dry: в БД не пишу")
            return result
        store.save_draft(plan)
        print("сохранён план", run_id)
        result["saved"] = True
        return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--accounts", nargs="*", default=None, choices=list(CABINETS))
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    run_plan(a.as_of, a.dry, a.accounts, a.json)


if __name__ == "__main__":
    main()
