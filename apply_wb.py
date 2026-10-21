"""Применяющий скрипт WB.

    python apply_wb.py                                    # dry-run: разница с живыми склейками и проверки, на WB ничего не пишется
    python apply_wb.py --run 2026-10-21-wb --json ops.json
    python apply_wb.py --refresh --env <путь к .env> --merge-env <путь>   # сначала перечитать склейки с WB (только чтение)
    python apply_wb.py --approve --by <имя>               # согласовать последний план (статус approved), запись в аудит
    python apply_wb.py --execute --confirm <run_id> --env <.env> --merge-env <.env с токенами склеек> [--accounts skazka] [--max-ops 1500]
    python apply_wb.py --rollback <run_id> --execute --confirm <run_id> --env <.env> --merge-env <.env>

Запись возможна только при статусе плана approved, с явным --execute и --confirm <run_id>. Операции строятся из живого состояния, поэтому
повторный запуск после обрыва делает только остаток. Перед записью всегда проверяется предполётный отчёт (applier/wb_preflight.py).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date

import psycopg

from applier.executor import PgJournal
from applier.wb_client import WbPort, api_for, merge_accounts, read_env_files
from applier.wb_executor import WbSettings, execute_wb, rollback_wb
from applier.wb_ops import WbDryRun, dry_run_wb
from applier.wb_preflight import evaluate_wb, render_wb
from plan_ozon import database_url
from planner.store import PgStore
from planner.wb_reader import read_snapshots


def read_live(conn) -> dict[tuple[str, int], int]:
    with conn.cursor() as cur:
        cur.execute("select account, nm_id, imt_id from gluing.wb_card")
        out = {(a, int(n)): int(i) for a, n, i in cur.fetchall()}
    conn.rollback()
    return out


def check_plan_wb(conn, plan) -> WbDryRun:
    """Сравнение плана с живыми склейками (только чтение БД)."""
    return dry_run_wb(plan, read_live(conn))


def snapshots_now(conn, plan) -> dict[str, str]:
    """Даты снимков на момент проверки: остатки и рулоны из БД, склейки WB — когда их перечитывали последний раз."""
    accounts = sorted({g.account for g in plan.groups})
    snaps = read_snapshots(conn, accounts[0])
    lives = [read_snapshots(conn, a)["wb_live_at"] for a in accounts]
    conn.rollback()
    snaps["wb_live_at"] = min(lives) if all(lives) else ""          # самый старый из кабинетов
    return snaps


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", default="", help="run_id плана; по умолчанию последний план WB")
    ap.add_argument("--refresh", action="store_true", help="перечитать склейки с WB (чтение) перед проверкой")
    ap.add_argument("--env", default="", help=".env с ключами WB (или MARKETPLACE_ENV)")
    ap.add_argument("--merge-env", default="", help=".env с токенами склеек WB (или MARKETPLACE_MERGE_ENV)")
    ap.add_argument("--json", default="")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--by", default="")
    ap.add_argument("--execute", action="store_true", help="ЗАПИСАТЬ на WB (нужны статус approved и --confirm)")
    ap.add_argument("--confirm", default="")
    ap.add_argument("--rollback", default="")
    ap.add_argument("--accounts", default="", help="через запятую: skazka,milky_garden,timeless")
    ap.add_argument("--max-ops", type=int, default=1500)
    ap.add_argument("--no-probe", dest="probe", action="store_false")
    a = ap.parse_args()

    with psycopg.connect(database_url(), autocommit=True) as conn:
        store = PgStore(conn)
        if a.rollback:
            if not (a.execute and a.confirm == a.rollback):
                raise SystemExit("для отката нужны --execute и --confirm <тот же run_id>")
            plan = store.load(a.rollback)
            env, menv = read_env_files(a.env, a.merge_env)
            journal = PgJournal(conn)
            nm = {(g.account, m.offer_id): int(m.platform_ids["nm_id"]) for g in plan.groups for d in g.designs for m in d.members}
            accounts = {r["account"] for r in journal.rows(a.rollback, "done")}
            port = WbPort({acc: api_for(acc, env, menv) for acc in accounts})
            r = rollback_wb(a.rollback, {acc: port for acc in accounts}, journal, nm, WbSettings())
            print(json.dumps(r, ensure_ascii=False, indent=1))
            if not journal.rows(a.rollback, "done"):
                store.set_status(a.rollback, "reverted", a.by or "rollback")
            return

        plan = store.load(a.run) if a.run else store.last("wb")
        if plan is None:
            raise SystemExit("план не найден")
        rid = plan.run.run_id
        accounts = sorted({x.strip() for x in a.accounts.split(",") if x.strip()} or {g.account for g in plan.groups})
        if a.refresh:
            from planner.wb_live import refresh_wb_cards
            env, menv = read_env_files(a.env, a.merge_env)
            for acc in accounts:
                print("склейки обновлены:", refresh_wb_cards(conn, acc, api_for(acc, env, menv)))
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

        res = check_plan_wb(conn, plan)
        res.ops = [o for o in res.ops if o.account in accounts]
        # согласованный (approved) и частично применённый (partial) план проверяется как черновик
        merge_accs = None
        if a.execute:
            _, menv0 = read_env_files(a.env, a.merge_env)
            merge_accs = merge_accounts(menv0)
        rep = evaluate_wb(plan, res, "draft" if status in ("draft", "approved", "partial") else status, snapshots_now(conn, plan), date.today(),
                          limits={"max_ops": a.max_ops}, merge_accounts=merge_accs)
        print(f"план {rid}, статус {status}; режим: {'ЗАПИСЬ' if a.execute else 'dry-run, на WB ничего не отправляется'}")
        print(render_wb(rep))
        for b in res.blocked[:15]:
            print("  ЗАБЛОКИРОВАНА", b.group_id, "|", "; ".join(b.reasons)[:200])
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump({"summary": res.summary(), "go": rep.go, "items": [i.__dict__ for i in rep.items], "metrics": rep.metrics,
                           "ops": [asdict(o) for o in res.ops], "blocked": [asdict(b) for b in res.blocked]}, f, ensure_ascii=False, indent=1)
        if not a.execute:
            return
        if status not in ("approved", "partial"):          # partial: повторный запуск после обрыва делает только остаток
            raise SystemExit(f"запись только для согласованного плана (статус {status}): сначала --approve --by <имя>")
        if a.confirm != rid:
            raise SystemExit(f"для записи повторите run_id: --confirm {rid}")
        if not rep.go:
            raise SystemExit("предполётная проверка не пройдена, запись отменена")
        if not res.ops:
            raise SystemExit("нечего применять")
        env, menv = read_env_files(a.env, a.merge_env)
        port = WbPort({acc: api_for(acc, env, menv) for acc in {o.account for o in res.ops}})
        store.set_status(rid, "applying", a.by or "apply")
        out = execute_wb(rid, res.ops, {acc: port for acc in {o.account for o in res.ops}}, PgJournal(conn),
                         WbSettings(probe=a.probe, max_ops=a.max_ops), res.sizes)
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        r = out.get("result", {})
        ok = r.get("done", 0) == sum(r.values()) and not out.get("stopped")
        status_after = "applied" if ok else ("failed" if out.get("stopped") else "partial")
        store.set_status(rid, status_after, a.by or "apply")
        publish_applied(rid, res, out, status_after)


def publish_applied(rid: str, res, out: dict, status: str) -> None:
    """Итог применения в шину (для уведомлений); сбой шины не должен ронять применение."""
    try:
        from busclient import PgBus
        from ops.bus_worker import bus_url
        bus = PgBus(bus_url())
        try:
            bus.publish("gluing.wb.applied", key=rid, payload={
                "run_id": rid, "status": status, "ops": len(res.ops), "result": out.get("result", {}),
                "stopped": out.get("stopped"), "summary": res.summary(), "members": res.members}, producer="apply_wb")
        finally:
            bus.close()
    except Exception as e:                                           # noqa: BLE001
        print("итог в шину не опубликован:", e)


if __name__ == "__main__":
    main()
