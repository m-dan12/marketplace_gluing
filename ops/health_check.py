"""Проверка здоровья ночной цепочки (запускать по расписанию раз в 10 минут на proftex-server).

    python ops/health_check.py            # проверить и опубликовать ops.alert (не чаще раза в час на вид проблемы)
    python ops/health_check.py --print    # только показать, ничего не публиковать

Проверяет: «мёртвые» сообщения шины, зависшие в очереди дольше 20 минут (в том числе когда laya-server не забирает расчёт), просроченные
блокировки, пульс локальных подписчиков, отсутствие события синка и плана к утреннему часу, применение, идущее дольше 3 часов."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BEAT_ROLES = ("attrs", "apply", "notify", "wb")          # у plan пульс лежит на laya-server и сюда не виден
BEAT_MAX_AGE = 300                                 # секунд
SYNC_BY_HOUR = 9
PLAN_BY_HOUR = 9


def alerts(s: dict, now: datetime) -> list[tuple[str, str]]:
    """s: снимок состояния (см. collect). -> [(вид проблемы, текст)]."""
    out: list[tuple[str, str]] = []
    for c, n in sorted((s.get("dead") or {}).items()):
        out.append((f"dead-{c}", f"в шине {n} необработанных сообщений подписчика {c} (статус dead), нужен разбор и retry"))
    for c, minutes in sorted((s.get("stale") or {}).items()):
        out.append((f"stale-{c}", f"подписчик {c} не забирает сообщения уже {int(minutes)} мин"))
    for c, n in sorted((s.get("expired") or {}).items()):
        out.append((f"expired-{c}", f"{n} сообщений подписчика {c} зависли в обработке (истекла блокировка)"))
    for role, age in sorted((s.get("beats") or {}).items()):
        if age is None:
            out.append((f"beat-{role}", f"воркер {role}: нет файла пульса, процесс не запускался?"))
        elif age > BEAT_MAX_AGE:
            out.append((f"beat-{role}", f"воркер {role} молчит {int(age)} с"))
    if now.hour >= SYNC_BY_HOUR and not s.get("sync_today", True):
        out.append(("no-sync", f"к {SYNC_BY_HOUR}:00 нет события «синк готов» за сегодня: db_sync не закончился или шлюз не сработал"))
    if now.hour >= PLAN_BY_HOUR and not s.get("plan_today", True):
        out.append(("no-plan", f"к {PLAN_BY_HOUR}:00 нет плана склеек за сегодня (laya-server или шина)"))
    if now.hour >= SYNC_BY_HOUR and not s.get("sync_wb_today", True):
        out.append(("no-sync-wb", f"к {SYNC_BY_HOUR}:00 нет события «синк WB готов» за сегодня: db_sync не закончился или шлюз не сработал"))
    if now.hour >= PLAN_BY_HOUR and not s.get("plan_wb_today", True):
        out.append(("no-plan-wb", f"к {PLAN_BY_HOUR}:00 нет плана склеек WB за сегодня (подписчик wb или шина)"))
    for run in s.get("apply_stuck") or []:
        out.append((f"apply-stuck-{run}", f"применение {run} идёт больше 3 часов без итога"))
    return out


def collect(bus_conn, data_conn, now: datetime) -> dict:
    def rows(conn, sql):
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    s: dict = {}
    s["dead"] = dict(rows(bus_conn, "select consumer, count(*) from bus.delivery where status='dead' and finished_at > now() - interval '2 days' group by 1"))
    s["stale"] = {c: float(m) for c, m in rows(bus_conn, "select consumer, extract(epoch from now() - min(available_at)) / 60 from bus.delivery "
                                                         "where status='pending' and available_at < now() - interval '20 minutes' group by 1")}
    s["expired"] = dict(rows(bus_conn, "select consumer, count(*) from bus.delivery where status='running' and locked_until < now() group by 1"))
    s["sync_today"] = bool(rows(bus_conn, "select 1 from bus.event where topic='sync.ozon.done' and created_at::date = current_date limit 1"))
    s["sync_wb_today"] = bool(rows(bus_conn, "select 1 from bus.event where topic='sync.wb.done' and created_at::date = current_date limit 1"))
    s["apply_stuck"] = [r[0] for r in rows(bus_conn, "select e.key from bus.event e where e.topic in ('gluing.ozon.apply_started', 'gluing.wb.apply_started') "
                                                    "and e.created_at < now() - interval '3 hours' and e.created_at > now() - interval '2 days' "
                                                    "and not exists (select 1 from bus.event a where a.topic in ('gluing.ozon.applied', 'gluing.wb.applied') and a.key = e.key)")]
    s["plan_today"] = bool(rows(data_conn, "select 1 from gluing.run where platform='ozon' and created_at::date = current_date limit 1"))
    s["plan_wb_today"] = bool(rows(data_conn, "select 1 from gluing.run where platform='wb' and created_at::date = current_date limit 1"))
    beats = {}
    for role in BEAT_ROLES:
        f = ROOT / "logs" / f"heartbeat_{role}.txt"
        try:
            beats[role] = (now - datetime.fromisoformat(f.read_text().strip())).total_seconds()
        except (OSError, ValueError):
            beats[role] = None
    s["beats"] = beats
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="only_print", action="store_true")
    a = ap.parse_args()
    import psycopg
    from busclient import PgBus
    from ops.bus_worker import bus_url
    from plan_ozon import database_url
    now = datetime.now()
    with psycopg.connect(bus_url()) as bc, psycopg.connect(database_url()) as dc:
        found = alerts(collect(bc, dc, now), now)
        bc.rollback()
        dc.rollback()
    for kind, text in found:
        print("ПРОБЛЕМА:", kind, "-", text)
    if not found:
        print("всё в порядке")
    if found and not a.only_print:
        bus = PgBus(bus_url())
        try:
            for kind, text in found:
                bus.publish("ops.alert", key=f"{kind}:{now:%Y%m%d%H}", payload={"kind": kind, "text": text}, producer="health")
        finally:
            bus.close()
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
