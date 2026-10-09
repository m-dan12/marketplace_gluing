"""CP-SAT: оптимальное разбиение ключей одной категории и бренда на склейки.

Решатель выбирает, сколько карточек каждого ключа попадёт в каждую группу.
Жёстко: каждая карточка ровно в одной группе, размер группы не больше лимита.
Мягко (штрафы): число групп, группы меньше минимума, разброс ключей внутри группы,
лишнее дробление ключа, неравномерные размеры.
Какие именно карточки ключа попадут в каждую группу, решаем потом «веером» по заказам.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .model import Card

try:
    from ortools.sat.python import cp_model
except ImportError:  # решатель необязателен: без него работает жадный сборщик
    cp_model = None


@dataclass
class KeyItem:
    label: str          # ключ, например 0-0-26
    cards: list[Card]
    pos: int            # место ключа среди соседей (ранг в отсортированном списке)


DEFAULT_WEIGHTS = {"groups": 100, "small_min": 5000, "small_soft": 300, "span": 40, "pair": 30, "spread": 5}


def available() -> bool:
    return cp_model is not None


def _spread_by_orders(cards: list[Card], counts: list[int]) -> list[list[Card]]:
    """Раздаём карточки ключа по группам «веером»: хиты и неликвид достаются каждой группе."""
    ordered = sorted(cards, key=lambda c: (-c.orders30, c.article))
    left = list(counts)
    total = [max(c, 1) for c in counts]
    parts: list[list[Card]] = [[] for _ in counts]
    for c in ordered:
        g = max((i for i in range(len(left)) if left[i] > 0), key=lambda i: (left[i] / total[i], left[i]))
        parts[g].append(c)
        left[g] -= 1
    return parts


def solve_pool(items: list[KeyItem], profile: dict, hint: list[list[tuple[str, int]]] | None = None) -> tuple[list[list[tuple[str, list[Card]]]] | None, str]:
    """Возвращает (группы, статус). В группе — пары (ключ, карточки). Группы None — нужен запасной сборщик."""
    if cp_model is None:
        return None, "нет решателя"
    lim = profile["limits"]
    cfg = profile.get("solver", {})
    w = {**DEFAULT_WEIGHTS, **cfg.get("weights", {})}
    mx, mn, soft = lim["max_group"], lim["min_group"], lim["soft_min_group"]
    n = [len(it.cards) for it in items]
    total = sum(n)
    keys = len(items)
    groups = max(math.ceil(total / mx), len(hint) + 2 if hint else keys + math.ceil(total / mx))
    big = max(total, keys, mx)

    m = cp_model.CpModel()
    y = [[m.NewIntVar(0, min(n[k], mx), f"y{k}_{g}") for g in range(groups)] for k in range(keys)]
    present = [[m.NewBoolVar(f"p{k}_{g}") for g in range(groups)] for k in range(keys)]
    used = [m.NewBoolVar(f"u{g}") for g in range(groups)]
    size = [m.NewIntVar(0, mx, f"s{g}") for g in range(groups)]
    s_min = [m.NewBoolVar(f"sm{g}") for g in range(groups)]
    s_soft = [m.NewBoolVar(f"ss{g}") for g in range(groups)]
    lo = [m.NewIntVar(0, keys, f"lo{g}") for g in range(groups)]
    hi = [m.NewIntVar(0, keys, f"hi{g}") for g in range(groups)]
    span = [m.NewIntVar(0, keys, f"sp{g}") for g in range(groups)]

    for k in range(keys):
        m.Add(sum(y[k]) == n[k])
        for g in range(groups):
            m.Add(y[k][g] <= n[k] * present[k][g])
            m.Add(y[k][g] >= present[k][g])
            m.Add(hi[g] >= items[k].pos).OnlyEnforceIf(present[k][g])
            m.Add(lo[g] <= items[k].pos).OnlyEnforceIf(present[k][g])
    for g in range(groups):
        m.Add(size[g] == sum(y[k][g] for k in range(keys)))
        m.Add(size[g] <= mx * used[g])
        m.Add(size[g] >= used[g])
        m.Add(size[g] + big * (1 - used[g]) + big * s_min[g] >= mn)
        m.Add(size[g] + big * (1 - used[g]) + big * s_soft[g] >= soft)
        m.Add(span[g] >= hi[g] - lo[g] - keys * (1 - used[g]))
        if g + 1 < groups:                      # порядок групп не важен: убираем симметрию
            m.Add(size[g] >= size[g + 1])

    biggest = m.NewIntVar(0, mx, "max")
    smallest = m.NewIntVar(0, mx, "min")
    for g in range(groups):
        m.Add(biggest >= size[g])
        m.Add(smallest <= size[g] + mx * (1 - used[g]))

    if hint:                                    # стартовое решение: жадный сборщик, группы по убыванию размера
        index = {it.label: k for k, it in enumerate(items)}
        for g, grp in enumerate(sorted(hint, key=lambda gr: -sum(c for _, c in gr))):
            given = {index[lbl]: c for lbl, c in grp}
            for k in range(keys):
                m.AddHint(y[k][g], given.get(k, 0))
                m.AddHint(present[k][g], 1 if k in given else 0)
            m.AddHint(used[g], 1)
        for g in range(len(hint), groups):
            m.AddHint(used[g], 0)
            for k in range(keys):
                m.AddHint(y[k][g], 0)
                m.AddHint(present[k][g], 0)

    m.Minimize(
        w["groups"] * sum(used) + w["small_min"] * sum(s_min) + w["small_soft"] * sum(s_soft)
        + w["span"] * sum(span) + w["pair"] * sum(sum(r) for r in present) + w["spread"] * (biggest - smallest)
    )

    solver = cp_model.CpSolver()
    # детерминированный режим: один и тот же вход даёт те же склейки (лимит в условных единицах, не в секундах)
    solver.parameters.num_workers = int(cfg.get("workers", 8))
    solver.parameters.interleave_search = True
    solver.parameters.max_deterministic_time = float(cfg.get("deterministic_limit", 20))
    solver.parameters.random_seed = 1
    solver.parameters.relative_gap_limit = float(cfg.get("gap", 0.01))
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, "решение не найдено"
    label = "оптимально" if status == cp_model.OPTIMAL else "лучшее из найденных за лимит времени"

    counts = [[solver.Value(y[k][g]) for g in range(groups)] for k in range(keys)]
    per_key = []
    for k in range(keys):
        gs = [g for g in range(groups) if counts[k][g] > 0]
        parts = _spread_by_orders(items[k].cards, [counts[k][g] for g in gs])
        per_key.append(dict(zip(gs, parts)))
    result = []
    for g in range(groups):
        pairs = [(items[k].label, per_key[k][g]) for k in range(keys) if g in per_key[k]]
        if pairs:
            result.append(pairs)
    return result, label
