"""Бэктест прогноза спроса по дизайнам Озона: для нескольких дат T прогнозируем заказы на 28 дней вперёд по данным до T
и сравниваем с фактом. Только чтение из БД.   python -m tools.backtest_demand [--horizon 28]"""
from __future__ import annotations

import argparse
import statistics as st
from collections import defaultdict
from datetime import date, timedelta

import psycopg

from gluing.parsing import parse_article
from plan_ozon import database_url
from planner.demand import forecast, spearman, top_precision, wape

VARIANTS = [
    ("naive28 (как сейчас)", dict(method="naive", window=28)),
    ("naive14", dict(method="naive", window=14)),
    ("ewma hl=7", dict(method="ewma", half_life=7, window=28)),
    ("ewma hl=14", dict(method="ewma", half_life=14, window=28)),
    ("avail28", dict(method="avail", window=28)),
    ("avail_ewma hl=7", dict(method="avail_ewma", half_life=7, window=28)),
    ("avail_ewma hl=14", dict(method="avail_ewma", half_life=14, window=28)),
    ("avail28 + подтяжка 14", dict(method="avail", window=28, shrink=14)),
    ("avail_ewma hl=14 + подтяжка 14", dict(method="avail_ewma", half_life=14, window=28, shrink=14)),
    ("avail_ewma hl=14 + подтяжка 28", dict(method="avail_ewma", half_life=14, window=28, shrink=28)),
]


def load(conn):
    cur = conn.cursor()
    daily: dict = defaultdict(dict)
    cur.execute("select account, article, day, units from orders_daily where marketplace='ozon'")
    for a, o, d, u in cur.fetchall():
        daily[(a, o)][d] = daily[(a, o)].get(d, 0) + int(u or 0)
    avail: dict = defaultdict(dict)
    cur.execute("select account, article, snapshot_date, ozon_listed, ozon_out from article_stock_daily")
    for a, o, d, listed, out in cur.fetchall():
        avail[(a, o)][d] = bool(listed) and not bool(out)
    cur.execute("select account, article, category from product_cards where marketplace='ozon'")
    typ = {(a, o): (c or "?").split(" > ")[-1] for a, o, c in cur.fetchall()}
    conn.rollback()
    return daily, avail, typ


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=28)
    a = ap.parse_args()
    with psycopg.connect(database_url()) as conn:
        daily, avail, typ = load(conn)
    last = max(d for v in daily.values() for d in v)
    first = min(d for v in daily.values() for d in v)
    folds = []
    t = first + timedelta(days=28)
    while t + timedelta(days=a.horizon) <= last + timedelta(days=1):
        folds.append(t)
        t += timedelta(days=14)
    print(f"заказы {first}..{last}; точек отсчёта T: {[str(x) for x in folds]}; горизонт {a.horizon} дней")
    design_of, group_of = {}, {}
    for k in set(daily) | set(typ):
        p = parse_article(k[1])
        if not p["design"]:
            continue
        design_of[k] = (k[0], p["prefix"] + p["design"], typ.get(k, "?"))
        group_of[k] = (k[0], typ.get(k, "?"))
    res: dict = {name: defaultdict(list) for name, _ in VARIANTS}
    sizes = []
    for T in folds:
        end = T + timedelta(days=a.horizon)
        actual: dict = defaultdict(float)
        for k, dd in daily.items():
            if k in design_of:
                actual[design_of[k]] += sum(v for day, v in dd.items() if T <= day < end)
        active = {design_of[k] for k, dd in daily.items() if k in design_of and any(T - timedelta(days=56) <= day < end for day in dd)}
        sizes.append(len(active))
        for name, params in VARIANTS:
            pred = forecast(daily, avail, group_of, design_of, T, horizon=a.horizon, **params)
            pr = {d: pred.get(d, 0.0) for d in active}
            ac = {d: actual.get(d, 0.0) for d in active}
            res[name]["WAPE"].append(wape(pr, ac))
            res[name]["Spearman"].append(spearman(pr, ac))
            res[name]["топ-20%"].append(top_precision(pr, ac))
    print(f"дизайнов в оценке по точкам: {sizes}")
    print(f"{'метод':36s} {'WAPE':>7s} {'Spearman':>9s} {'топ-20%':>8s}")
    for name, m in res.items():
        print(f"{name:36s} {st.mean(m['WAPE']):7.3f} {st.mean(m['Spearman']):9.3f} {st.mean(m['топ-20%']):8.3f}")
    print("по точкам (WAPE):")
    for name, m in res.items():
        print(f"  {name:36s}", " ".join(f"{x:6.3f}" for x in m["WAPE"]))


if __name__ == "__main__":
    main()
