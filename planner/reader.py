"""Чтение исходных данных Озона из схемы public (только SELECT). Соединение — psycopg, роль gluing_app."""
from __future__ import annotations

from gluing.ozon.loader import build_designs, skus_from_rows

# Карточки Озона + остаток последнего снимка (FBO+FBS) + заказы за окно. Остаток 0 не отбрасываем: ткани получают отрезы из рулона.
CARDS_SQL = """
select c.account, c.article as offer_id, c.title, c.category, c.external_id as product_id,
       coalesce(s.stock, 0)::int as stock, coalesce(o.q, 0)::int as orders28
from product_cards c
left join (select account, offer_id, sum(present) as stock from ozon_stocks
           where snapshot_date = (select max(snapshot_date) from ozon_stocks) group by 1, 2) s
       on s.account = c.account and s.offer_id = c.article
left join (select account, offer_id, sum(quantity) as q from ozon_orders
           where order_date >= %(as_of)s::date - %(window)s group by 1, 2) o
       on o.account = c.account and o.offer_id = c.article
where c.marketplace = 'ozon'
"""

# Рулоны тканей Профтекса: склад FBS Селсапа, артикул PT<номер>, количество в метрах (последний снимок).
ROLLS_SQL = """
select article, sum(quantity)::numeric as meters from selsup_stocks
where snapshot_date = (select max(snapshot_date) from selsup_stocks)
  and warehouse_name = 'FBS' and article ~ '^PT[0-9]+$'
group by 1
"""

SNAPSHOTS_SQL = """
select (select max(snapshot_date) from ozon_stocks), (select max(snapshot_date) from selsup_stocks)
"""


def read_rows(conn, as_of: str, window_days: int = 28) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(CARDS_SQL, {"as_of": as_of, "window": window_days})
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def read_rolls(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(ROLLS_SQL)
        return {a: int(m) for a, m in cur.fetchall()}


def read_snapshots(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(SNAPSHOTS_SQL)
        st, sel = cur.fetchone()
    return {"ozon_stock_snapshot": str(st), "rolls_snapshot": str(sel)}


def load_from_db(conn, profile: dict, as_of: str, window_days: int = 28):
    """-> (дизайны, неизвестные бренды, platform_ids {(account, offer_id): {"product_id": ...}}, inputs)."""
    rows = read_rows(conn, as_of, window_days)
    skus, accounts = skus_from_rows(rows, read_rolls(conn))
    pids = {(r["account"], r["offer_id"]): {"product_id": r["product_id"]} for r in rows}
    designs, unknown = build_designs(skus, accounts, profile)
    inputs = {**read_snapshots(conn), "orders_window_days": window_days, "as_of": as_of}
    return designs, unknown, pids, inputs
