import itertools

from gluing.ozon import parts_solver
from gluing.ozon.model import Design, OzonSku


def design(i, orders=0, sigs=()):
    s = OzonSku(offer_id=f"PT{i}/4-13-26/1", title="", category="", stock=5, orders28=orders, prefix="PT",
                design_no=str(i), key="4-13-26", key_nums=("4", "13", "26"))
    return Design(account="a", brand="b", typ="t", design=f"PT{i}", digits=str(i), skus=[s], sigs=frozenset(sigs))


def solve(ds, k, prev=None, **kw):
    return parts_solver.solve_parts(ds, k, "a|b|t|Обычные", prev or {}, 18, 6, **kw)


def test_sizes_within_bounds_and_every_design_once():
    ds = [design(i, orders=i % 7) for i in range(30)]
    parts = solve(ds, 2)
    assert sorted(d.design for p in parts.values() for d in p) == sorted(d.design for d in ds)
    assert all(6 <= len(p) <= 18 for p in parts.values())


def test_twins_never_share_a_part():
    ds = [design(i, orders=i % 5, sigs=[("t", i // 2)] if i < 10 else []) for i in range(30)]   # пары (0,1), (2,3) ... близнецы
    parts = solve(ds, 2)
    for p in parts.values():
        for a, b in itertools.combinations(p, 2):
            assert not (a.sigs & b.sigs)


def test_stickiness_keeps_previous_parts_when_balanced():
    ds = [design(i, orders=(i % 4) * 3) for i in range(30)]
    prev = {d.uid: f"a|b|t|Обычные#{1 if i < 15 else 2}" for i, d in enumerate(ds)}
    parts = solve(ds, 2, prev)
    assert {d.design for d in parts[1]} == {f"PT{i}" for i in range(15)}


def test_power_is_balanced_between_parts():
    ds = [design(i, orders=100 if i < 4 else 1) for i in range(30)]                 # 4 тяжёлых дизайна
    parts = solve(ds, 2)
    heavy = [sum(1 for d in p if d.orders == 100) for p in parts.values()]
    assert sorted(heavy) == [2, 2]


def test_every_part_gets_a_hit_and_a_dead_design():
    ds = [design(i, orders=0 if i % 2 else 50 + i) for i in range(36)]
    for p in solve(ds, 2).values():
        assert any(d.orders > 0 for d in p) and any(d.orders == 0 for d in p)


def test_deterministic():
    ds = [design(i, orders=(i * 7) % 13) for i in range(40)]
    a, b = solve(ds, 3), solve(ds, 3)
    assert {k: [d.design for d in v] for k, v in a.items()} == {k: [d.design for d in v] for k, v in b.items()}


def test_infeasible_returns_none_and_fallback_to_greedy(monkeypatch):
    ds = [design(i) for i in range(30)]
    assert parts_solver.solve_parts(ds, 1, "p", {}, 18, 6) is None                    # одной части решатель не нужен
    monkeypatch.setattr(parts_solver, "cp_model", None)
    assert solve(ds, 2) is None
