"""Проверки результата: жёсткие нарушения и сомнительные места."""
from __future__ import annotations

from .model import Group


def hard_violations(groups: list[Group], profile: dict) -> list[str]:
    out: list[str] = []
    mx = profile["limits"]["max_group"]
    seen: set[str] = set()
    for g in groups:
        if len(g.cards) > mx:
            out.append(f"{g.gid}: {len(g.cards)} SKU больше лимита {mx}")
        if len({c.brand for c in g.cards}) > 1:
            out.append(f"{g.gid}: смешаны бренды")
        if len({c.category for c in g.cards}) > 1:
            out.append(f"{g.gid}: смешаны категории")
        for c in g.cards:
            if c.article in seen:
                out.append(f"{c.article}: в нескольких склейках")
            seen.add(c.article)
    return out


def soft_flags(groups: list[Group], profile: dict) -> dict[str, list[str]]:
    mn = profile["limits"]["min_group"]
    flags: dict[str, list[str]] = {"small": [], "no_sales": [], "multi_key": []}
    for g in groups:
        if len(g.cards) < mn:
            flags["small"].append(g.gid)
        if all(c.orders30 == 0 for c in g.cards):
            flags["no_sales"].append(g.gid)
        if len({c.key for c in g.cards}) > 1:
            flags["multi_key"].append(g.gid)
    return flags


def quality(groups: list[Group], profile: dict) -> dict:
    """Метрики для сравнения сборщиков: меньше групп, меньше малых и «разбросанных» по ключам — лучше."""
    lim = profile["limits"]
    # место ключа среди соседей: ранг среди ключей той же категории и бренда
    ranks: dict[tuple[str, str], dict[str, int]] = {}
    for g in groups:
        for c in g.cards:
            ranks.setdefault((g.brand, g.category), {})[c.key] = 0
    for k, d in ranks.items():
        order = sorted(d, key=lambda s: tuple(int(x) for x in s.replace("-", " ").split()) if s else (10**9,))
        ranks[k] = {s: i for i, s in enumerate(order)}
    span = multi = 0
    for g in groups:
        r = [ranks[(g.brand, g.category)][c.key] for c in g.cards]
        if len({c.key for c in g.cards}) > 1:
            multi += 1
            span += max(r) - min(r)
    sizes = [len(g.cards) for g in groups]
    return {
        "groups": len(groups),
        "small<min": sum(1 for s in sizes if s < lim["min_group"]),
        "small<soft": sum(1 for s in sizes if s < lim["soft_min_group"]),
        "multi_key": multi,
        "key_span_total": span,
        "avg_size": round(sum(sizes) / len(sizes), 1),
        "size_range": f"{min(sizes)}..{max(sizes)}",
    }
