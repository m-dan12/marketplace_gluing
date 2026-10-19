"""Шлюз «синк готов»: если данные для склеек за сегодня загружены, публикует sync.ozon.done (один раз в сутки).

    python ops/sync_gate.py            # проверить и, если данные свежие, опубликовать событие
    python ops/sync_gate.py --check    # только проверить, ничего не публиковать

Данные считаются свежими, когда снимок остатков Озона и снимок рулонов Селсапа не старше сегодняшнего дня, а заказы Озона
есть за вчера или сегодня. Код возврата db_sync не используется: его команда `sync all` может падать на посторонних шагах (ML).
Ключ события — дата, повторный запуск ничего не меняет. Запускать по расписанию каждые 10–15 минут после времени db_sync.
Настройки: DATABASE_URL_GLUING и BUS_DATABASE_URL (окружение или .env в корне проекта)."""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FRESH_SQL = """
select (select max(snapshot_date) from ozon_stocks), (select max(snapshot_date) from selsup_stocks),
       (select max(order_date)::date from ozon_orders)
"""


def check(stocks, rolls, orders, today: date) -> tuple[bool, list[str]]:
    """-> (свежие ли данные, список причин, почему нет)."""
    why = []
    if stocks is None or stocks < today:
        why.append(f"остатки Озона на {stocks}, нужны на {today}")
    if rolls is None or rolls < today:
        why.append(f"рулоны Селсапа на {rolls}, нужны на {today}")
    if orders is None or orders < today - timedelta(days=1):
        why.append(f"последний заказ Озона {orders}, нужен не старше {today - timedelta(days=1)}")
    return not why, why


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    import psycopg
    from busclient import PgBus
    from ops.bus_worker import bus_url
    from plan_ozon import database_url

    today = date.today()
    with psycopg.connect(database_url()) as conn:
        stocks, rolls, orders = conn.execute(FRESH_SQL).fetchone()
    ok, why = check(stocks, rolls, orders, today)
    print(f"{today}: данные {'свежие' if ok else 'ещё не готовы: ' + '; '.join(why)}")
    if not ok or a.check:
        return 0
    bus = PgBus(bus_url())
    try:
        eid, created = bus.publish("sync.ozon.done", key=today.isoformat(),
                                   payload={"as_of": today.isoformat(), "ozon_stocks": str(stocks), "rolls": str(rolls),
                                            "last_order": str(orders)}, producer="sync-gate")
    finally:
        bus.close()
    print("событие опубликовано" if created else "событие за сегодня уже было", f"(id {eid})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
