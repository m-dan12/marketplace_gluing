"""Подписчики шины событий для склеек Озона (ночная цепочка).

    db_sync (proftex)  --sync.ozon.done-->  attrs (proftex): обновить атрибуты с Озона (только чтение Озона)
                       --gluing.ozon.attrs_ready-->  plan (laya): построить план, сохранить черновик
                       --gluing.ozon.plan_ready-->   apply (proftex): dry-run по живым атрибутам
                       --gluing.ozon.dryrun_ready--> (уведомление / согласование; запись на Озон здесь не выполняется)

    python ops/bus_worker.py attrs|plan|apply [--loop] [--poll 60]   # без --loop: разобрать очередь и выйти
    python ops/bus_worker.py publish <топик> [--key К] [--payload '{"dry": true}']
    python ops/bus_worker.py init       # один раз: зарегистрировать подписчиков (до этого события им не доставляются)
    python ops/bus_worker.py status

На laya запускать через туннель:  python ops/laya_run.py -- python ops/bus_worker.py plan
payload.dry = true — сквозной тест без записи: атрибуты не обновляются, план не сохраняется, dry-run не публикуется.
Настройки (окружение или .env в корне проекта): BUS_DATABASE_URL; для attrs ещё MARKETPLACE_ENV — путь к .env с ключами Озона."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from busclient import PgBus, Router, Worker  # noqa: E402

log = logging.getLogger("bus_worker")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def conf(name: str) -> str:
    """Значение из окружения или из .env в корне проекта (пусто, если нет)."""
    val = os.environ.get(name, "")
    env = ROOT / ".env"
    if not val and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                val = line.split("=", 1)[1].strip()
    return val


def bus_url() -> str:
    url = conf("BUS_DATABASE_URL")
    if not url:
        raise SystemExit("нет BUS_DATABASE_URL (окружение или .env)")
    return url


def profile_of() -> dict:
    import tomllib
    with open(ROOT / "profiles" / "ozon.toml", "rb") as f:
        return tomllib.load(f)


def as_of_of(ev) -> str:
    v = str(ev.payload.get("as_of") or ev.key or "")
    return v if DATE.fullmatch(v) else date.today().isoformat()


def guard(fn):
    """SystemExit из рабочих скриптов превращаем в обычную ошибку, чтобы подписчик не умирал, а сообщение ушло на повтор."""
    def wrapper(ev):
        try:
            return fn(ev)
        except SystemExit as e:
            raise RuntimeError(str(e) or "SystemExit") from None
    return wrapper


def handler_attrs(bus):
    def h(ev):
        dry = bool(ev.payload.get("dry"))
        if dry:
            stats = {"пропущено": "dry"}
        else:
            import psycopg
            from applier.ozon_client import env_path
            from plan_ozon import database_url
            from planner.ozon_attrs import run as refresh
            with psycopg.connect(database_url()) as conn:
                stats = refresh(conn, str(env_path(conf("MARKETPLACE_ENV"))), None, False)
        bus.publish("gluing.ozon.attrs_ready", key=ev.key,
                    payload={"as_of": as_of_of(ev), "stats": stats, "dry": dry}, producer="gluing-attrs", trace_id=str(ev.id))
        log.info("атрибуты обновлены (dry=%s): %s", dry, stats)
    return guard(h)


def handler_plan(bus):
    def h(ev):
        if ev.key and bus.find("gluing.ozon.plan_ready", ev.key):
            log.info("план для ключа %s уже построен, пропуск", ev.key)
            return
        from plan_ozon import run_plan
        dry = bool(ev.payload.get("dry"))
        res = run_plan(as_of_of(ev), dry=dry)
        bus.publish("gluing.ozon.plan_ready", key=ev.key, payload={**res, "as_of": as_of_of(ev), "dry": dry},
                    producer="gluing-plan", trace_id=ev.trace_id or str(ev.id))
        log.info("план %s (dry=%s): %s склеек", res["run_id"], dry, res["groups"])
    return guard(h)


def handler_apply(bus):
    def h(ev):
        if ev.payload.get("dry"):
            log.info("тестовое событие (dry), dry-run не выполняю")
            return
        if ev.key and bus.find("gluing.ozon.dryrun_ready", ev.key):
            log.info("dry-run для ключа %s уже выполнен, пропуск", ev.key)
            return
        import psycopg
        from apply_ozon import check_plan
        from applier.autoapply import decide, load_config
        from applier.brands import load_brand_map
        from applier.preflight import evaluate
        from plan_ozon import database_url
        from planner.store import PgStore
        rid = ev.payload["run_id"]
        prof = profile_of()
        with psycopg.connect(database_url()) as conn:
            store = PgStore(conn)
            plan = store.load(rid)
            if plan is None:
                raise RuntimeError(f"план {rid} не найден")
            status = store.status(rid)
            res = check_plan(conn, plan, load_brand_map(prof))
        trace = ev.trace_id or str(ev.id)
        bus.publish("gluing.ozon.dryrun_ready", key=ev.key,
                    payload={"run_id": rid, "status": status, "summary": res.summary(), "as_of": ev.payload.get("as_of")},
                    producer="gluing-apply", trace_id=trace)
        log.info("dry-run плана %s: %s", rid, res.summary())

        # --- автоприменение: решение принимает предполётный отчёт, любое «нет» становится событием apply_blocked
        if ev.key and any(bus.find(t, ev.key) for t in ("gluing.ozon.apply_started", "gluing.ozon.applied", "gluing.ozon.apply_blocked")):
            return
        from ops import apply_runner
        snaps = {k: v for k, v in plan.run.inputs.items() if k in ("ozon_stock_snapshot", "rolls_snapshot")}
        rep = evaluate(plan, res, status, snaps, date.today())
        cfg = load_config(prof)
        d = decide(rep, len(res.ops), cfg, (ROOT / cfg.kill_file).exists(), apply_runner.is_running() if cfg.enabled else False)
        info = {"run_id": rid, "as_of": ev.payload.get("as_of"), "ops": len(res.ops), "summary": res.summary(),
                "warns": [i.name for i in rep.warns], "members": rep.metrics.get("members")}
        if d.action == "off":
            log.info("автоприменение выключено: план %s ждёт ручного согласования", rid)
        elif d.action == "skip_empty":
            bus.publish("gluing.ozon.applied", key=ev.key or rid, payload={**info, "status": status, "result": {}},
                        producer="gluing-apply", trace_id=trace)
            log.info("план %s: изменений не нужно", rid)
        elif d.action == "blocked":
            bus.publish("gluing.ozon.apply_blocked", key=ev.key or rid, payload={**info, "reasons": d.reasons},
                        producer="gluing-apply", trace_id=trace)
            log.warning("автоприменение плана %s остановлено: %s", rid, "; ".join(d.reasons))
        else:
            with psycopg.connect(database_url(), autocommit=True) as conn:
                PgStore(conn).set_status(rid, "approved", "auto")
            apply_runner.start(rid, str(env_path_of()), cfg.max_ops)
            bus.publish("gluing.ozon.apply_started", key=ev.key or rid, payload=info, producer="gluing-apply", trace_id=trace)
            log.info("автоприменение плана %s запущено: %s операций", rid, len(res.ops))
    return guard(h)


def env_path_of():
    from applier.ozon_client import env_path
    return env_path(conf("MARKETPLACE_ENV"))


def handler_notify(bus):
    from ops import notify
    client = notify.from_conf(conf)
    if client is None:
        log.warning("YANDEX_BOT_TOKEN / YANDEX_CHAT_ID не заданы: уведомления пишутся в logs/notify.log")
    return notify.make_handler(client, ROOT / "logs" / "notify.log")


def _notify_topics() -> list[str]:
    from ops import notify
    return notify.TOPICS


WB_TOPICS = ["sync.wb.done", "gluing.wb.live_ready", "gluing.wb.plan_ready"]


def _wb_live(bus):
    """sync.wb.done -> перечитать склейки (imtID) всех кабинетов с WB (только чтение) -> gluing.wb.live_ready."""
    def h(ev):
        if ev.key and bus.find("gluing.wb.live_ready", ev.key):
            log.info("склейки WB для ключа %s уже перечитаны, пропуск", ev.key)
            return
        dry = bool(ev.payload.get("dry"))
        stats: object = {"пропущено": "dry"}
        if not dry:
            import psycopg
            from applier.wb_client import ACCOUNT_ENV, api_for, read_env_files
            from plan_ozon import database_url
            from planner.wb_live import refresh_wb_cards
            env, menv = read_env_files(conf("MARKETPLACE_ENV"), conf("MARKETPLACE_MERGE_ENV"))
            with psycopg.connect(database_url(), autocommit=True) as conn:
                stats = [refresh_wb_cards(conn, acc, api_for(acc, env, menv)) for acc in ACCOUNT_ENV]
        bus.publish("gluing.wb.live_ready", key=ev.key, payload={"as_of": as_of_of(ev), "stats": stats, "dry": dry},
                    producer="gluing-wb", trace_id=ev.trace_id or str(ev.id))
        log.info("склейки WB перечитаны (dry=%s): %s", dry, stats)
    return guard(h)


def _wb_plan(bus):
    """gluing.wb.live_ready -> построить и сохранить черновик плана WB -> gluing.wb.plan_ready."""
    def h(ev):
        if ev.key and bus.find("gluing.wb.plan_ready", ev.key):
            log.info("план WB для ключа %s уже построен, пропуск", ev.key)
            return
        from plan_wb import run_plan
        dry = bool(ev.payload.get("dry"))
        res = run_plan(as_of_of(ev), dry=dry)
        bus.publish("gluing.wb.plan_ready", key=ev.key, payload={**res, "as_of": as_of_of(ev), "dry": dry},
                    producer="gluing-wb", trace_id=ev.trace_id or str(ev.id))
        log.info("план WB %s (dry=%s): %s склеек", res["run_id"], dry, res["groups"])
    return guard(h)


def _wb_apply(bus):
    """gluing.wb.plan_ready -> предполётная проверка; решение принимает applier.autoapply.decide (профиль, стоп-файл, блокировки)."""
    def h(ev):
        if ev.payload.get("dry"):
            log.info("тестовое событие (dry), проверку WB не выполняю")
            return
        if ev.key and any(bus.find(t, ev.key) for t in ("gluing.wb.apply_started", "gluing.wb.applied", "gluing.wb.apply_blocked")):
            log.info("план WB для ключа %s уже обработан, пропуск", ev.key)
            return
        import psycopg
        from apply_wb import check_plan_wb, snapshots_now
        from applier.autoapply import decide, load_config
        from applier.wb_client import merge_accounts, read_env_files
        from applier.wb_preflight import evaluate_wb
        from gluing.wb.profile import load_profile
        from ops import apply_runner
        from plan_ozon import database_url
        from planner.store import PgStore
        rid = ev.payload["run_id"]
        cfg = load_config(load_profile("wb_proftex"))
        _, menv = read_env_files(conf("MARKETPLACE_ENV"), conf("MARKETPLACE_MERGE_ENV"))
        with psycopg.connect(database_url()) as conn:
            store = PgStore(conn)
            plan = store.load(rid)
            if plan is None:
                raise RuntimeError(f"план {rid} не найден")
            status = store.status(rid)
            res = check_plan_wb(conn, plan)
            snaps = snapshots_now(conn, plan)
        trace = ev.trace_id or str(ev.id)
        rep = evaluate_wb(plan, res, status, snaps, date.today(), limits={"max_ops": cfg.max_ops}, merge_accounts=merge_accounts(menv))
        info = {"run_id": rid, "as_of": ev.payload.get("as_of"), "ops": len(res.ops), "summary": res.summary(),
                "warns": [i.name for i in rep.warns], "members": rep.metrics.get("members")}
        d = decide(rep, len(res.ops), cfg, (ROOT / cfg.kill_file).exists(),
                   apply_runner.is_running(apply_runner.WB_TASK_NAME) if cfg.enabled else False)
        key = ev.key or rid
        if d.action == "off":
            log.info("автоприменение WB выключено: план %s ждёт ручного согласования", rid)
        elif d.action == "skip_empty":
            bus.publish("gluing.wb.applied", key=key, payload={**info, "status": status, "result": {}}, producer="gluing-wb", trace_id=trace)
            log.info("план WB %s: изменений не нужно", rid)
        elif d.action == "blocked":
            bus.publish("gluing.wb.apply_blocked", key=key, payload={**info, "reasons": d.reasons}, producer="gluing-wb", trace_id=trace)
            log.warning("автоприменение плана WB %s остановлено: %s", rid, "; ".join(d.reasons))
        else:
            with psycopg.connect(database_url(), autocommit=True) as conn:
                PgStore(conn).set_status(rid, "approved", "auto")
            apply_runner.start_wb(rid, conf("MARKETPLACE_ENV"), conf("MARKETPLACE_MERGE_ENV"), cfg.max_ops)
            bus.publish("gluing.wb.apply_started", key=key, payload=info, producer="gluing-wb", trace_id=trace)
            log.info("автоприменение плана WB %s запущено: %s операций", rid, len(res.ops))
    return guard(h)


def handler_wb(bus):
    stages = {"sync.wb.done": _wb_live(bus), "gluing.wb.live_ready": _wb_plan(bus), "gluing.wb.plan_ready": _wb_apply(bus)}

    def h(ev):
        return stages[ev.topic](ev)
    return h


ROLES = {
    "wb": ("gluing-wb", WB_TOPICS, handler_wb),
    "attrs": ("gluing-attrs", ["sync.ozon.done"], handler_attrs),
    "plan": ("gluing-plan", ["gluing.ozon.attrs_ready"], handler_plan),
    "apply": ("gluing-apply", ["gluing.ozon.plan_ready"], handler_apply),
    "notify": ("gluing-notify", _notify_topics(), handler_notify),
}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("role", choices=[*ROLES, "publish", "status", "init"])
    ap.add_argument("topic", nargs="?")
    ap.add_argument("--key", default=None)
    ap.add_argument("--payload", default="{}")
    ap.add_argument("--loop", action="store_true", help="работать постоянно (по умолчанию — разобрать очередь и выйти)")
    ap.add_argument("--poll", type=float, default=60)
    a = ap.parse_args()
    bus = PgBus(bus_url())
    try:
        if a.role == "publish":
            if not a.topic:
                raise SystemExit("укажите топик")
            print(bus.publish(a.topic, a.key, json.loads(a.payload), producer="cli"))
            return 0
        if a.role == "init":
            for name, patterns, _ in ROLES.values():
                bus.register(name, patterns, max_attempts=3 if name != "gluing-notify" else 8, visibility_sec=3600)
                print("зарегистрирован", name, ", ".join(patterns))
            return 0
        if a.role == "status":
            for role, (name, _, _) in ROLES.items():
                beat_file = ROOT / "logs" / f"heartbeat_{role}.txt"
                age = ""
                if beat_file.exists():
                    age = f", пульс {int((datetime.now() - datetime.fromisoformat(beat_file.read_text().strip())).total_seconds())} с назад"
                print(name, bus.status(name), age)
            return 0
        name, patterns, make = ROLES[a.role]
        router = Router()
        handler = make(bus)
        for pattern in patterns:
            router.on(pattern)(handler)
        w = Worker(bus, name, router, max_attempts=3 if a.role != "notify" else 8, visibility_sec=3600)
        if a.loop:
            beat_file = ROOT / "logs" / f"heartbeat_{a.role}.txt"
            beat_file.parent.mkdir(exist_ok=True)

            def beat() -> bool:                               # пульс: файл с временем последней итерации цикла
                beat_file.write_text(datetime.now().isoformat(timespec="seconds"), encoding="ascii")
                return False

            log.info("%s: слушаю шину (LISTEN), страховочный опрос раз в %s с", name, a.poll)
            w.run_forever(a.poll, stop=beat)
        else:
            print(f"{name}: обработано {w.run_once()}")
        return 0
    finally:
        bus.close()


if __name__ == "__main__":
    sys.exit(main())
