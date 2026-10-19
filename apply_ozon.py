"""Применяющий скрипт Озона.

    python apply_ozon.py                                  # dry-run: разница с живым состоянием и проверки, на Озон ничего не пишется
    python apply_ozon.py --run 2026-10-13-ozon --json ops.json
    python apply_ozon.py --refresh --env <путь к .env>     # сначала перечитать атрибуты с Озона (только чтение)
    python apply_ozon.py --approve --by <имя>              # согласовать последний план (статус approved), запись в аудит
    python apply_ozon.py --execute --confirm <run_id> --env <путь к .env> [--accounts skazka] [--types Скатерть] [--only set_model_name] [--max-ops 1500]
    python apply_ozon.py --rollback <run_id> --execute --confirm <run_id> --env <путь к .env>

Запись возможна только при статусе плана approved, с явным --execute и --confirm <run_id>. Путь к .env с ключами
передаётся параметром --env или переменной MARKETPLACE_ENV; в репозитории ключей и путей нет.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

import psycopg

from applier.executor import PgJournal, Settings, execute, rollback
from applier.ops import dry_run, select_ops
from applier.ozon_client import api_for, env_path, read_env
from plan_ozon import database_url
from planner.store import PgStore
from planner.twins import load_aspects, load_attrs


def show(res) -> None:
    for k, v in res.summary().items():
        print(f"{k}: {v}")
    for b in res.blocked[:15]:
        print("  ЗАБЛОКИРОВАНА", b.group_id, "|", "; ".join(b.reasons)[:200])
    for w in res.warnings[:10]:
        print("  предупреждение:", w[:200])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="", help="run_id плана; по умолчанию последний план Озона")
    ap.add_argument("--refresh", action="store_true", help="перечитать атрибуты с Озона (чтение) перед проверкой")
    ap.add_argument("--env", default="", help=".env с ключами Озона (или MARKETPLACE_ENV)")
    ap.add_argument("--json", default="")
    ap.add_argument("--approve", action="store_true", help="согласовать план")
    ap.add_argument("--by", default="", help="кто согласует (для аудита)")
    ap.add_argument("--execute", action="store_true", help="ЗАПИСАТЬ на Озон (нужны статус approved и --confirm)")
    ap.add_argument("--confirm", default="", help="повторите run_id плана, чтобы подтвердить запись")
    ap.add_argument("--rollback", default="", help="run_id, чьи применённые операции нужно откатить (вместе с --execute)")
    ap.add_argument("--accounts", default="", help="через запятую: skazka,timeless,milky_garden (по умолчанию все)")
    ap.add_argument("--types", default="", help="только эти типы товара, через запятую (снятия unglue_model по типу не фильтруются и отбрасываются)")
    ap.add_argument("--only", default="", help="только эти виды операций: set_color_name, set_model_name, unglue_model")
    ap.add_argument("--max-ops", type=int, default=1500, help="предохранитель: больше операций за запуск не применять")
    ap.add_argument("--no-probe", dest="probe", action="store_false", help="не делать пробную запись одной операции")
    a = ap.parse_args()

    with psycopg.connect(database_url(), autocommit=True) as conn:       # каждая запись журнала фиксируется сразу
        store = PgStore(conn)
        if a.rollback:
            if not (a.execute and a.confirm == a.rollback):
                raise SystemExit("для отката нужны --execute и --confirm <тот же run_id>")
            env = read_env(env_path(a.env))
            journal = PgJournal(conn)
            accounts = {r["account"] for r in journal.rows(a.rollback, "done")}
            r = rollback(a.rollback, {acc: api_for(acc, env) for acc in accounts}, journal, Settings())
            print(json.dumps(r, ensure_ascii=False, indent=1))
            if not journal.rows(a.rollback, "done"):
                store.set_status(a.rollback, "reverted", a.by or "rollback")
            return

        if a.refresh:
            from planner.ozon_attrs import run as refresh
            print("обновлено:", refresh(conn, str(env_path(a.env)), None, False))
        plan = store.load(a.run) if a.run else store.last("ozon")
        if plan is None:
            raise SystemExit("план не найден")
        rid = plan.run.run_id
        status = store.status(rid)

        if a.approve:
            if not a.by:
                raise SystemExit("--approve требует --by <имя>")
            if status != "draft":
                raise SystemExit(f"согласовать можно только черновик, статус плана {rid}: {status}")
            if plan.checks.hard_violations:
                raise SystemExit("в плане есть жёсткие нарушения, согласование невозможно")
            store.set_status(rid, "approved", a.by)
            print(f"план {rid} согласован ({a.by})")
            return

        with conn.cursor() as cur:
            cur.execute("select account, offer_id, sum(present) from ozon_stocks where snapshot_date=(select max(snapshot_date) from ozon_stocks) group by 1,2")
            stock = {(a_, o_): int(s_ or 0) for a_, o_, s_ in cur.fetchall()}
        res = dry_run(plan, load_attrs(conn), load_aspects(conn), stock)
        conn.rollback()
        accounts = {x.strip() for x in a.accounts.split(",") if x.strip()}
        types = {x.strip() for x in a.types.split(",") if x.strip()}
        gtype = {g.group_id: g.type for g in plan.groups}
        only = {x.strip() for x in a.only.split(",") if x.strip()}
        res.ops = select_ops(res.ops, gtype, accounts, types, only)
        print(f"план {rid}, статус {status}; режим: {'ЗАПИСЬ' if a.execute else 'dry-run, на Озон ничего не отправляется'}")
        show(res)
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"summary": res.summary(), "ops": [asdict(o) for o in res.ops],
                           "blocked": [asdict(b) for b in res.blocked], "warnings": res.warnings}, f, ensure_ascii=False, indent=1)
        if not a.execute:
            return
        if status != "approved":
            raise SystemExit(f"запись только для согласованного плана (статус {status}): сначала --approve --by <имя>")
        if a.confirm != rid:
            raise SystemExit(f"для записи повторите run_id: --confirm {rid}")
        if not res.ops:
            raise SystemExit("нечего применять")
        env = read_env(env_path(a.env))
        apis = {acc: api_for(acc, env) for acc in {o.account for o in res.ops}}
        store.set_status(rid, "applying", a.by or "apply")
        out = execute(rid, res.ops, apis, PgJournal(conn), Settings(probe=a.probe, max_ops=a.max_ops))
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        r = out.get("result", {})
        ok = r.get("done", 0) == sum(r.values()) and not out.get("stopped")
        store.set_status(rid, "applied" if ok else ("failed" if out.get("stopped") else "partial"), a.by or "apply")


if __name__ == "__main__":
    main()
