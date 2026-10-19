"""Прогноз спроса по дизайнам: сколько заказов ожидается за горизонт (по умолчанию 28 дней).

Оценщики простые и объяснимые, выбираются бэктестом (tools/backtest_demand.py), а не на глаз:
  naive      — заказы за последние `window` дней (то, что было до прогноза);
  ewma       — экспоненциально взвешенная скорость (свежие дни важнее, период полураспада `half_life`);
  avail      — заказы за окно, делённые на дни, когда товар был доступен (в день без остатка продаж не бывает);
  shrink     — оценка «подтягивается» к средней скорости группы (кабинет × тип) пропорционально малости данных.
Скорость считается по артикулам и суммируется в дизайн.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta

# daily[(account, offer)] -> {day: units}; avail[(account, offer)] -> {day: bool}. Нет данных о наличии — день считается доступным.


def _window(daily: dict, key, start: date, end: date) -> float:
    d = daily.get(key, {})
    return sum(v for day, v in d.items() if start <= day < end)


def _avail_days(avail: dict, key, start: date, end: date) -> int:
    a = avail.get(key)
    total = (end - start).days
    if not a:
        return total
    return sum(1 for i in range(total) if a.get(start + timedelta(days=i), True))


def rate_naive(daily, avail, key, asof: date, window: int = 28, **_) -> float:
    return _window(daily, key, asof - timedelta(days=window), asof) / window


def rate_ewma(daily, avail, key, asof: date, half_life: float = 14.0, window: int = 56, **_) -> float:
    num = den = 0.0
    d = daily.get(key, {})
    for i in range(1, window + 1):
        day = asof - timedelta(days=i)
        w = 0.5 ** ((i - 1) / half_life)
        num += w * d.get(day, 0)
        den += w
    return num / den


def rate_avail(daily, avail, key, asof: date, window: int = 28, min_days: int = 7, **_) -> float:
    start = asof - timedelta(days=window)
    days = max(_avail_days(avail, key, start, asof), min_days)
    return _window(daily, key, start, asof) / days


def rate_avail_ewma(daily, avail, key, asof: date, half_life: float = 14.0, window: int = 56, **_) -> float:
    """EWMA только по доступным дням: недоступный день не считается нулём, а выпадает из средней."""
    num = den = 0.0
    d, a = daily.get(key, {}), avail.get(key) or {}
    for i in range(1, window + 1):
        day = asof - timedelta(days=i)
        if not a.get(day, True):
            continue
        w = 0.5 ** ((i - 1) / half_life)
        num += w * d.get(day, 0)
        den += w
    return num / den if den else 0.0


METHODS = {"naive": rate_naive, "ewma": rate_ewma, "avail": rate_avail, "avail_ewma": rate_avail_ewma}


def forecast(daily: dict, avail: dict, group_of: dict, design_of: dict, asof: date, method: str = "avail_ewma",
             horizon: int = 28, shrink: float = 0.0, **params) -> dict:
    """-> {design_key: ожидаемые заказы за horizon дней}.

    group_of[(acc, offer)] — группа для подтягивания (кабинет × тип); design_of[(acc, offer)] — ключ дизайна.
    shrink — вес «априорной» средней группы в днях (0 — без подтягивания)."""
    fn = METHODS[method]
    keys = list(design_of)
    rates = {k: fn(daily, avail, k, asof, **params) for k in keys}
    if shrink > 0:
        by_group: dict = defaultdict(list)
        for k in keys:
            by_group[group_of.get(k)].append(rates[k])
        prior = {g: sum(v) / len(v) for g, v in by_group.items()}
        window = params.get("window", 28)
        for k in keys:
            n_eff = _avail_days(avail, k, asof - timedelta(days=window), asof)
            p = prior[group_of.get(k)]
            rates[k] = (rates[k] * n_eff + p * shrink) / (n_eff + shrink)
    out: dict = defaultdict(float)
    for k in keys:
        out[design_of[k]] += rates[k] * horizon
    return dict(out)


# ---------- метрики качества прогноза ----------

def wape(pred: dict, actual: dict) -> float:
    keys = set(pred) | set(actual)
    tot = sum(actual.get(k, 0) for k in keys)
    return sum(abs(pred.get(k, 0) - actual.get(k, 0)) for k in keys) / tot if tot else 0.0


def _ranks(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        for t in range(i, j + 1):
            ranks[order[t]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(pred: dict, actual: dict) -> float:
    keys = sorted(set(pred) | set(actual))
    a, b = _ranks([pred.get(k, 0) for k in keys]), _ranks([actual.get(k, 0) for k in keys])
    n = len(keys)
    if n < 3:
        return 0.0
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else 0.0


def top_precision(pred: dict, actual: dict, share: float = 0.2) -> float:
    """Доля предсказанных «хитов» (верхние share по прогнозу), которые оказались хитами по факту."""
    keys = sorted(set(pred) | set(actual))
    n = max(1, int(len(keys) * share))
    top_p = set(sorted(keys, key=lambda k: (-pred.get(k, 0), k))[:n])
    top_a = set(sorted(keys, key=lambda k: (-actual.get(k, 0), k))[:n])
    return len(top_p & top_a) / n
