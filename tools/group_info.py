"""Состав группы плана: артикулы с живыми брендом, моделью и названием из карточек Озона (только чтение БД).

    python -m tools.group_info g_48f3587c [--run 2026-10-20-ozon]"""
from __future__ import annotations

import argparse
from collections import Counter

import psycopg

from plan_ozon import database_url
from planner.store import PgStore

BRAND, MODEL = "85", "9048"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("group_id")
    ap.add_argument("--run", default="")
    a = ap.parse_args()
    with psycopg.connect(database_url()) as conn:
        plan = PgStore(conn).load(a.run) if a.run else PgStore(conn).last("ozon")
        g = next((x for x in plan.groups if x.group_id == a.group_id), None)
        if g is None:
            raise SystemExit(f"группы {a.group_id} нет в плане {plan.run.run_id}")
        offers = [(d.design, m) for d in g.designs for m in d.members]
        with conn.cursor() as cur:
            cur.execute("select offer_id, attrs, name from gluing.sku_attr where account=%s and offer_id = any(%s)",
                        (g.account, [m.offer_id for _, m in offers]))
            live = {o: (at, nm) for o, at, nm in cur.fetchall()}
        conn.rollback()
    print(f"план {plan.run.run_id}, группа {g.group_id}: кабинет {g.account}, бренд по плану «{g.brand}», тип {g.type}, ветка {g.branch}, "
          f"ключ {g.key}, дизайнов {g.n_designs}, артикулов {g.n_skus}")
    brands = Counter()
    print(f"\n{'артикул':<28}{'бренд на Озоне':<16}{'модель на Озоне':<18}{'остаток':>8}{'заказов':>9}  название")
    for design, m in sorted(offers, key=lambda x: (x[0], x[1].offer_id)):
        at, nm = live.get(m.offer_id, ({}, "(нет в sku_attr)"))
        brand = (at.get(BRAND) or [""])[0] or "(пусто)"
        model = (at.get(MODEL) or [""])[0]
        brands[brand] += 1
        print(f"{m.offer_id:<28}{brand:<16}{model[:17]:<18}{m.stock:>8}{m.orders_28d:>9}  {(nm or '')[:70]}")
    print("\nБренды на Озоне в группе:", dict(brands))


if __name__ == "__main__":
    main()
