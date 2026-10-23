"""Чтение исходных данных WB из схемы public и gluing.wb_card (только SELECT)."""
from __future__ import annotations

TOTAL_ROWS = ("Всего находится на складах", "В пути до получателей", "В пути возвраты на склад WB")   # итоги и «в пути» не остаток
WB_RF = "Склад WB РФ"

CARDS_SQL = """
select c.account, c.article as offer_id, c.external_id as nm_id, c.title, c.category, c.brand as wb_brand,
       w.imt_id, coalesce(st.q, 0)::int as stock_wb, coalesce(fb.q, 0)::int as stock_fbs, coalesce(o.q, 0)::int as orders30,
       (%(as_of)s::date - greatest(la.d, lf.d))::int as out_days
from product_cards c
left join gluing.wb_card w on w.account = c.account and w.nm_id = c.external_id
left join (select account, nm_id, sum(quantity) as q from wb_stocks
           where snapshot_date = (select max(snapshot_date) from wb_stocks)
             and case when %(mode)s = 'wb_rf' then warehouse_name = %(rf)s
                      else warehouse_name <> all(%(skip)s) end
           group by 1, 2) st on st.account = c.account and st.nm_id = c.external_id
left join (select article, sum(quantity) as q from selsup_stocks
           where snapshot_date = (select max(snapshot_date) from selsup_stocks) and warehouse_name = 'FBS'
           group by 1) fb on fb.article = c.article
left join (select account, nm_id, count(*) as q from wb_orders
           where order_date >= %(as_of)s::date - %(window)s and not coalesce(is_cancel, false) group by 1, 2) o
       on o.account = c.account and o.nm_id = c.external_id
left join (select account, nm_id, max(snapshot_date) as d
           from (select account, nm_id, snapshot_date, sum(quantity) as q from wb_stocks
                 where snapshot_date >= %(as_of)s::date - %(grace)s
                   and case when %(mode)s = 'wb_rf' then warehouse_name = %(rf)s
                            else warehouse_name <> all(%(skip)s) end
                 group by 1, 2, 3) x
           where q > 0 group by 1, 2) la on la.account = c.account and la.nm_id = c.external_id
left join (select article, max(snapshot_date) as d
           from (select article, snapshot_date, sum(quantity) as q from selsup_stocks
                 where snapshot_date >= %(as_of)s::date - %(grace)s and warehouse_name = 'FBS' group by 1, 2) y
           where q > 0 group by 1) lf on lf.article = c.article
where c.marketplace = 'wb' and c.account = %(account)s
"""

IMT_SQL = "select nm_id, imt_id from gluing.wb_card where account = %s"

SNAPSHOTS_SQL = """
select (select max(snapshot_date) from wb_stocks), (select max(snapshot_date) from selsup_stocks),
       (select max(fetched_at)::date from gluing.wb_card where account = %s),
       (select max(fetched_at) from gluing.wb_card where account = %s)
"""


def read_wb_rows(conn, account: str, as_of: str, mode: str = "all_physical", window_days: int = 30,
                 grace_days: int = 0) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(CARDS_SQL, {"account": account, "as_of": as_of, "window": window_days, "mode": mode, "rf": WB_RF, "grace": grace_days,
                                "skip": [WB_RF, *TOTAL_ROWS]})
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def read_imt_members(conn, account: str) -> dict[int, set[int]]:
    """{imtID: все nm_id склейки на WB} — по живому состоянию, включая неактивные карточки."""
    out: dict[int, set[int]] = {}
    with conn.cursor() as cur:
        cur.execute(IMT_SQL, (account,))
        for nm, imt in cur.fetchall():
            out.setdefault(imt, set()).add(nm)
    return out


def read_snapshots(conn, account: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(SNAPSHOTS_SQL, (account, account))
        wb, sel, live, live_at = cur.fetchone()
    return {"wb_stock_snapshot": str(wb), "rolls_snapshot": str(sel), "wb_live_snapshot": str(live),
            "wb_live_at": live_at.isoformat() if live_at else ""}
