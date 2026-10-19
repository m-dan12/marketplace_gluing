"""Бэктест ML-прогноза (градиентный бустинг, Пуассон) против naive28. Обучение только на точках, чьё окно факта закончилось до T.
   python -m tools.backtest_demand_ml"""
from __future__ import annotations

import statistics as st
from collections import defaultdict
from datetime import timedelta

import numpy as np
import psycopg
from sklearn.ensemble import HistGradientBoostingRegressor

from gluing.parsing import parse_article
from plan_ozon import database_url
from planner.demand import spearman, top_precision, wape
from tools.backtest_demand import load

H = 28


def features(daily, avail, design_of, T, types, accs):
    agg = defaultdict(lambda: np.zeros(12))
    first = {}
    for k, dd in daily.items():
        if k not in design_of:
            continue
        d = design_of[k]
        row = agg[d]
        for day, v in dd.items():
            age = (T - day).days
            if age <= 0:
                continue
            if age <= 7: row[0] += v
            if age <= 14: row[1] += v
            if age <= 28: row[2] += v
            if age <= 56: row[3] += v
            if row[4] == 0 or age < row[4]: row[4] = age          # давность последнего заказа
            first[d] = max(first.get(d, 0), age)
    sk = defaultdict(int)
    for k in design_of:
        sk[design_of[k]] += 1
    for k, av in avail.items():
        if k not in design_of:
            continue
        d = design_of[k]
        n = sum(1 for i in range(1, 29) if av.get(T - timedelta(days=i), True))
        agg[d][5] += n / 28
    out = {}
    for d, row in agg.items():
        row = row.copy()
        row[6] = first.get(d, 0)
        row[7] = sk[d]
        row[8] = types.setdefault(d[2], len(types))
        row[9] = accs.setdefault(d[0], len(accs))
        row[10] = row[0] / 7 / (row[2] / 28 + 1e-6) if row[2] else 0        # динамика: неделя к месяцу
        out[d] = row
    return out


def actual_units(daily, design_of, T):
    a = defaultdict(float)
    for k, dd in daily.items():
        if k in design_of:
            a[design_of[k]] += sum(v for day, v in dd.items() if T <= day < T + timedelta(days=H))
    return a


def main():
    with psycopg.connect(database_url()) as conn:
        daily, avail, typ = load(conn)
    design_of = {}
    for k in set(daily) | set(typ):
        p = parse_article(k[1])
        if p["design"]:
            design_of[k] = (k[0], p["prefix"] + p["design"], typ.get(k, "?"))
    first = min(d for v in daily.values() for d in v)
    last = max(d for v in daily.values() for d in v)
    Ts, t = [], first + timedelta(days=28)
    while t + timedelta(days=H) <= last + timedelta(days=1):
        Ts.append(t)
        t += timedelta(days=7)
    types, accs = {}, {}
    data = {}
    for T in Ts:
        f, a = features(daily, avail, design_of, T, types, accs), actual_units(daily, design_of, T)
        keys = sorted(f)
        data[T] = (keys, np.array([f[k] for k in keys]), np.array([a.get(k, 0.0) for k in keys]))
    print("точки T (неделя):", [str(x) for x in Ts])
    rows = []
    for T in Ts:
        train = [x for x in Ts if x + timedelta(days=H) <= T]                 # окно факта обучающих точек закончилось до T
        if len(train) < 1:
            continue
        X = np.vstack([data[x][1] for x in train]); y = np.concatenate([data[x][2] for x in train])
        m = HistGradientBoostingRegressor(loss="poisson", max_iter=150, learning_rate=0.06, max_depth=4, min_samples_leaf=30, random_state=0)
        m.fit(X, y)
        keys, Xt, yt = data[T]
        act_mask = np.array([any(daily.get(k2, {}).get(d2) for k2 in [] ) for d2 in []]) if False else None
        pred = dict(zip(keys, m.predict(Xt)))
        naive = dict(zip(keys, Xt[:, 2]))                                      # заказы за 28 дней = прогноз на 28
        act = dict(zip(keys, yt))
        active = [k for k in keys if Xt[keys.index(k), 3] > 0 or act[k] > 0]
        pr, nv, ac = ({k: d[k] for k in active} for d in (pred, naive, act))
        rows.append((T, len(train), len(active), wape(nv, ac), wape(pr, ac), spearman(nv, ac), spearman(pr, ac), top_precision(nv, ac), top_precision(pr, ac)))
    print("T          обуч.точек  дизайнов   WAPE naive/ML      Spearman naive/ML   топ-20% naive/ML")
    for r in rows:
        print(f"{r[0]}  {r[1]:5d}  {r[2]:8d}   {r[3]:.3f} / {r[4]:.3f}     {r[5]:.3f} / {r[6]:.3f}      {r[7]:.3f} / {r[8]:.3f}")
    print("среднее:", " ".join(f"{st.mean(c):.3f}" for c in zip(*[r[3:] for r in rows])))


if __name__ == "__main__":
    main()
