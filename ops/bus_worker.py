"""Подписчики шины событий для склеек Озона (ночная цепочка).

    db_sync (proftex)  --sync.ozon.done-->  attrs (proftex): обновить атрибуты с Озона (только чтение Озона)
                       --gluing.ozon.attrs_ready-->  plan (laya): построить план, сохранить черновик
                       --gluing.ozon.plan_ready-->   apply (proftex): dry-run по живым атрибутам
                       --gluing.ozon.dryrun_ready--> (уведомление / согласование; запись на Озон здесь не выполняется)

    python ops/bus_worker.py attrs|plan|apply [--once | --loop] [--poll 60]
    python ops/bus_worker.py publish <топик> [--key К] [--payload '{"dry": true}']
    python ops/bus_worker.py init       # один раз: зарегистрировать подписчиков (до этого события им не доставляются)
    python ops/bus_worker.py status

На laya запускать через туннель:  python ops/laya_run.py -- python ops/bus_worker.py plan --once
payload.dry = true — сквозной тест без записи: атрибуты не обновляются, план не сохраняется, dry-run не публикуется.
Строка подключения к шине: BUS_DATABASE_URL (окружение или .env в корне проекта)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from busclient import PgBus, Router, Worker  # noqa: E402

log = logging.getLogger("bus_worker")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def bus_url() -> str:
    url = os.environ.get("BUS_DATABASE_URL")
    env = ROOT / ".env"
    if not url and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("BUS_DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if not url:
        raise SystemExit("нет BUS_DATABASE_URL (окружение или .env)")
    return url


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
                stats = refresh(conn, str(env_path("")), None, False)
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
        from plan_ozon import database_url
        from planner.store import PgStore
        rid = ev.payload["run_id"]
        with psycopg.connect(database_url()) as conn:
            store = PgStore(conn)
            plan = store.load(rid)
            if plan is None:
                raise RuntimeError(f"план {rid} не найден")
            status = store.status(rid)
            res = check_plan(conn, plan)
        bus.publish("gluing.ozon.dryrun_ready", key=ev.key,
                    payload={"run_id": rid, "status": status, "summary": res.summary(), "as_of": ev.payload.get("as_of")},
                    producer="gluing-apply", trace_id=ev.trace_id or str(ev.id))
        log.info("dry-run плана %s: %s", rid, res.summary())
    return guard(h)


ROLES = {
    "attrs": ("gluing-attrs", "sync.ozon.done", handler_attrs),
    "plan": ("gluing-plan", "gluing.ozon.attrs_ready", handler_plan),
    "apply": ("gluing-apply", "gluing.ozon.plan_ready", handler_apply),
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
            for name, pattern, _ in ROLES.values():
                bus.register(name, [pattern], max_attempts=3, visibility_sec=3600)
                print("зарегистрирован", name, pattern)
            return 0
        if a.role == "status":
            for name, _, _ in ROLES.values():
                print(name, bus.status(name))
            return 0
        name, pattern, make = ROLES[a.role]
        router = Router()
        router.on(pattern)(make(bus))
        w = Worker(bus, name, router, max_attempts=3, visibility_sec=3600)
        if a.loop:
            w.run_forever(a.poll)
        else:
            print(f"{name}: обработано {w.run_once()}")
        return 0
    finally:
        bus.close()


if __name__ == "__main__":
    sys.exit(main())
