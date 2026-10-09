"""Сборщик: корзины -> деление поровну -> объединение ключей (жадно или решателем CP-SAT)."""
from __future__ import annotations

import math
from collections import defaultdict

from . import solver as cpsat
from .model import Card, Group
from .rules import Bucket, Context, assign


def _key_sort(c: Card):
    return tuple(int(n) for n in c.key_nums) if c.key_nums else (10**9,)


def split_equal(cards: list[Card], max_size: int) -> list[list[Card]]:
    """Делим на равные части «веером» по заказам, чтобы хиты и неликвид попали в каждую часть."""
    if len(cards) <= max_size:
        return [cards]
    k = math.ceil(len(cards) / max_size)
    ordered = sorted(cards, key=lambda c: (-c.orders30, c.article))
    parts: list[list[Card]] = [[] for _ in range(k)]
    for i, c in enumerate(ordered):
        parts[i % k].append(c)
    return parts


def _greedy_pool(items: list[tuple[Bucket, list[Card]]], max_size: int) -> list[list[tuple[str, list[Card]]]]:
    """Большие ключи делим поровну, соседние по ключу корзины склеиваем, пока влезает в лимит."""
    items = sorted(items, key=lambda it: _key_sort(it[1][0]))
    bins: list[list[tuple[str, list[Card]]]] = []
    for b, cs in items:
        key = b.bid.split(":", 1)[1]
        for part in split_equal(cs, max_size) if len(cs) >= max_size else [cs]:
            if bins and sum(len(x[1]) for x in bins[-1]) + len(part) <= max_size:
                bins[-1].append((key, part))
            else:
                bins.append([(key, part)])
    return bins


def build(cards: list[Card], profile: dict, ctx: Context, solver: str = "greedy") -> list[Group]:
    max_size = profile["limits"]["max_group"]
    order = profile["rules"]["order"]
    use_cpsat = solver == "cpsat" and cpsat.available()

    buckets: dict[Bucket, list[Card]] = defaultdict(list)
    for c in cards:
        buckets[assign(c, ctx, order)].append(c)

    groups: list[Group] = []

    def add(b: Bucket, part: list[Card], rule: str | None = None, note: str = ""):
        g = Group(gid="", brand=b.brand, category=b.category, rule=rule or b.rule, cards=part)
        g.notes.append(note or b.why)
        groups.append(g)

    # неделимые по смыслу корзины делим поровну; ключи копим в пул категории и бренда
    pool: dict[tuple[str, str], list[tuple[Bucket, list[Card]]]] = defaultdict(list)
    for b, cs in buckets.items():
        if not b.mergeable:
            for part in split_equal(cs, max_size):
                add(b, part)
        elif len(cs) >= max_size and not use_cpsat:
            for part in split_equal(cs, max_size):
                add(b, part)
        else:
            pool[(b.brand, b.category)].append((b, cs))

    for (brand, cat), items in pool.items():
        bins = None
        used = "жадный"
        if use_cpsat:
            ordered = sorted(items, key=lambda it: _key_sort(it[1][0]))
            key_items = [cpsat.KeyItem(b.bid.split(":", 1)[1], cs, i) for i, (b, cs) in enumerate(ordered)]
            start = [[(k, len(cs)) for k, cs in gr] for gr in _greedy_pool(items, max_size)]
            bins, status = cpsat.solve_pool(key_items, profile, start)
            used = f"CP-SAT, {status}"
        if bins is None:
            bins = _greedy_pool(items, max_size)
            used = "жадный" if not use_cpsat else "жадный (CP-SAT не нашёл решения)"
        base = items[0][0]
        for bn in bins:
            part = [c for _, cs in bn for c in cs]
            keys = [k for k, _ in bn]
            if len(keys) == 1:
                add(base, part, rule=f"Ключ {keys[0]}", note=f"ключ {keys[0]}; сборщик: {used}")
            else:
                add(base, part, rule=" + ".join(f"Ключ {k}" for k in keys),
                    note=f"соседние ключи объединены: {', '.join(keys)}; сборщик: {used}")

    groups.sort(key=lambda g: (g.brand, g.category, g.rule))
    for i, g in enumerate(groups, 1):
        g.gid = f"WB-PF-{i:03d}"
    return groups
