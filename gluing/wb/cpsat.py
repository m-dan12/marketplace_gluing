"""CP-SAT для планировщика WB (по аналогии с решателем Озона). Два места, где жадный алгоритм заменяется решателем:

1. `solve_tails` — какие размеры-хвосты объединить в одну группу. Жёстко: размер группы не больше рабочего лимита; мягко (веса в
   профиле [solver.weights]): число групп, группы меньше минимума, разброс размеров внутри группы, лишнее дробление размера,
   неравные группы. Это общий решатель gluing.solver, стартовое решение — жадная раскладка (хуже неё не будет).
2. `solve_parts` — как разложить карточки одного набора по частям (живые склейки). Жёстко: размер части от минимума до места в
   склейке. Мягко: сколько карточек сменили склейку против текущего состояния (липкость), разброс размеров, хит и неликвид в каждой части.

Результат детерминирован: один поток и лимит по детерминированному времени. Нет ortools или он падает на процессоре (как на proftex-server):
функции возвращают None, и планировщик использует жадную раскладку."""
from __future__ import annotations

try:
    from ortools.sat.python import cp_model
except Exception:                                          # noqa: BLE001
    cp_model = None

from gluing import cpsat_probe
from gluing import solver as pool_solver
from gluing.model import Card

DEFAULT_PART_WEIGHTS = {"move": 60, "size": 15, "mix": 200}


def available() -> bool:
    return cp_model is not None and cpsat_probe.works()


def solve_tails(items: list[tuple[str, list[Card]]], profile: dict, hint: list[list[tuple[str, int]]] | None = None):
    """items — [(ключ размера, карточки)] в порядке соседства размеров. -> [[(ключ, карточки)]] группы или None."""
    if not items or not available():
        return None
    lim = profile["limits"]
    view = {"limits": {"max_group": lim.get("work_group", lim["max_group"]), "min_group": lim["min_group"],
                       "soft_min_group": lim.get("soft_min_group", lim["min_group"])},
            "solver": profile.get("solver", {})}
    ks = [pool_solver.KeyItem(label, cards, pos) for pos, (label, cards) in enumerate(items)]
    groups, _ = pool_solver.solve_pool(ks, view, hint)
    return groups


def solve_parts(cards: list[Card], room: list[int], prev: list[int | None], min_size: int, weights: dict | None = None,
                time_limit: float = 10.0) -> list[int] | None:
    """cards раскладываются по len(room) частям. room[j] — сколько карточек вмещает часть j; prev[i] — номер части, в которой карточка
    i лежит сейчас (None — нигде из частей). -> номер части для каждой карточки или None (нет решения / нет решателя)."""
    k, n = len(room), len(cards)
    if k < 2 or n == 0 or not available() or n > sum(room):
        return None
    w = {**DEFAULT_PART_WEIGHTS, **(weights or {})}
    lo = max(0, min(min_size, n // k))
    order = sorted(range(n), key=lambda i: (-cards[i].orders30, cards[i].article))
    n_hit = max(1, int(n * 0.2))
    hit = {i: r < n_hit and cards[i].orders30 > 0 for r, i in enumerate(order)}
    dead = [cards[i].orders30 == 0 for i in range(n)]

    m = cp_model.CpModel()
    x = [[m.NewBoolVar(f"x{i}_{j}") for j in range(k)] for i in range(n)]
    for i in range(n):
        m.AddExactlyOne(x[i])
    size = [sum(x[i][j] for i in range(n)) for j in range(k)]
    for j in range(k):
        m.Add(size[j] >= lo)
        m.Add(size[j] <= room[j])
    terms = []
    moved = []
    for i in range(n):
        if prev[i] is not None:
            mv = m.NewBoolVar(f"mv{i}")
            m.Add(mv + x[i][prev[i]] == 1)                 # mv = 1, если карточка не в своей текущей части
            moved.append(mv)
    if moved:
        terms.append(w["move"] * sum(moved))
    sh, sl = m.NewIntVar(0, n, "sh"), m.NewIntVar(0, n, "sl")
    for j in range(k):
        m.Add(sh >= size[j])
        m.Add(sl <= size[j])
    terms.append(w["size"] * (sh - sl))
    if sum(hit.values()) >= k and sum(dead) >= k:           # хит и неликвид в каждой части, если их хватает на все части
        for j in range(k):
            nh, nd = m.NewBoolVar(f"nh{j}"), m.NewBoolVar(f"nd{j}")
            m.Add(sum(x[i][j] for i in range(n) if hit[i]) + nh >= 1)
            m.Add(sum(x[i][j] for i in range(n) if dead[i]) + nd >= 1)
            terms.append(w["mix"] * (nh + nd))
    m.Minimize(sum(terms))
    for i in range(n):
        for j in range(k):
            m.AddHint(x[i][j], 1 if prev[i] == j else 0)
    s = cp_model.CpSolver()
    s.parameters.num_workers = 1
    s.parameters.max_deterministic_time = float(time_limit)
    s.parameters.random_seed = 1
    if s.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return [next(j for j in range(k) if s.Value(x[i][j])) for i in range(n)]
