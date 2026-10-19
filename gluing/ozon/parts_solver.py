"""Раскладка дизайнов ветки по k частям решателем CP-SAT (вместо жадной эвристики).

Жёсткие ограничения: каждый дизайн ровно в одной части; размер части от min до max; дизайны-близнецы (одинаковая подпись
варианта) не в одной части. Цель (минимизируется, веса в профиле [solver.weights]): сколько дизайнов сменили часть против
вчерашнего дня (липкость), разброс силы между частями, разброс размеров, части без хита или без неликвида.
Результат детерминирован: фиксированное зерно и лимит по детерминированному времени (как в решателе WB).
"""
from __future__ import annotations

try:
    from ortools.sat.python import cp_model
except Exception:                                          # ortools не установлен: вызывающий код вернётся к жадной раскладке
    cp_model = None

from gluing import cpsat_probe

from .model import Design

DEFAULT_WEIGHTS = {"move": 60, "balance": 2, "size": 15, "mix": 200}


def available() -> bool:
    return cp_model is not None and cpsat_probe.works()


def solve_parts(ds: list[Design], k: int, prefix: str, prev_asg: dict, mx: int, mn: int,
                weights: dict | None = None, time_limit: float = 10.0, hit_share: float = 0.2):
    """-> {номер части: [дизайны]} или None, если решения нет (тогда вызывающий использует жадную раскладку)."""
    if k < 2 or not ds or not available():
        return None
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    n = len(ds)
    lo = min(mn, n // k)                                  # если дизайнов маловато для минимума, смягчаем нижнюю границу
    if lo * k > n or n > mx * k:
        return None
    total = sum(d.power for d in ds) or 1.0
    power = [int(round(1000 * d.power / total)) for d in ds]      # доля силы ветки в промилле
    order = sorted(range(n), key=lambda i: (-ds[i].power, ds[i].design))
    n_hit = max(1, int(n * hit_share))
    hit = {i: (r < n_hit and ds[i].power > 0) for r, i in enumerate(order)}
    dead = [ds[i].power == 0 for i in range(n)]

    prev_idx: list[int | None] = []
    for d in ds:
        p = prev_asg.get(d.uid)
        prev_idx.append(int(p.rsplit("#", 1)[1]) if p and p.rsplit("#", 1)[0] == prefix else None)
    labels = sorted({x for x in prev_idx if x is not None})
    nxt = (labels[-1] + 1) if labels else 1
    while len(labels) < k:                                  # нужны ещё части
        labels.append(nxt)
        nxt += 1
    if len(labels) > k:                                     # частей стало меньше: оставляем самые наполненные вчера
        keep = sorted(labels, key=lambda x: (-sum(1 for v in prev_idx if v == x), x))[:k]
        labels = sorted(keep)
    pos = {lab: j for j, lab in enumerate(labels)}

    m = cp_model.CpModel()
    x = [[m.NewBoolVar(f"x{i}_{j}") for j in range(k)] for i in range(n)]
    for i in range(n):
        m.AddExactlyOne(x[i])
    size = [sum(x[i][j] for i in range(n)) for j in range(k)]
    for j in range(k):
        m.Add(size[j] >= lo)
        m.Add(size[j] <= mx)
    for a in range(n):
        for b in range(a + 1, n):
            if ds[a].sigs & ds[b].sigs:                     # близнецы: Озон не склеит их в одну модель
                for j in range(k):
                    m.Add(x[a][j] + x[b][j] <= 1)
    terms = []
    stay = []
    for i in range(n):
        if prev_idx[i] in pos:
            moved = m.NewBoolVar(f"mv{i}")
            m.Add(moved + x[i][pos[prev_idx[i]]] == 1)      # moved = 1, если дизайн не в своей вчерашней части
            stay.append(moved)
    terms.append(w["move"] * sum(stay) if stay else 0)
    pw = [sum(power[i] * x[i][j] for i in range(n)) for j in range(k)]
    hi, lw = m.NewIntVar(0, 1000, "hi"), m.NewIntVar(0, 1000, "lw")
    for j in range(k):
        m.Add(hi >= pw[j])
        m.Add(lw <= pw[j])
    terms.append(w["balance"] * (hi - lw))
    sh, sl = m.NewIntVar(0, n, "sh"), m.NewIntVar(0, n, "sl")
    for j in range(k):
        m.Add(sh >= size[j])
        m.Add(sl <= size[j])
    terms.append(w["size"] * (sh - sl))
    if sum(hit.values()) >= k and sum(dead) >= k:           # смешение хита и неликвида имеет смысл, только если их хватает на все части
        for j in range(k):
            nh, nd = m.NewBoolVar(f"nh{j}"), m.NewBoolVar(f"nd{j}")
            m.Add(sum(x[i][j] for i in range(n) if hit[i]) + nh >= 1)
            m.Add(sum(x[i][j] for i in range(n) if dead[i]) + nd >= 1)
            terms.append(w["mix"] * (nh + nd))
    m.Minimize(sum(terms))
    for i in range(n):                                       # подсказка: вчерашнее расположение
        for j in range(k):
            m.AddHint(x[i][j], 1 if prev_idx[i] is not None and pos.get(prev_idx[i]) == j else 0)

    s = cp_model.CpSolver()
    s.parameters.num_workers = 1                             # один поток + лимит по детерминированному времени: результат повторяем
    s.parameters.max_deterministic_time = float(time_limit)
    s.parameters.random_seed = 1
    status = s.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    out: dict[int, list[Design]] = {lab: [] for lab in labels}
    for i in range(n):
        for j in range(k):
            if s.Value(x[i][j]):
                out[labels[j]].append(ds[i])
    return out
