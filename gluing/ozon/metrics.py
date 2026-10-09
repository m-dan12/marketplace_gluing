"""Показатели качества результата Озона и сравнение двух запусков."""
from __future__ import annotations

import statistics as st
from collections import defaultdict

from .features import DEFAULT
from .model import OGroup


def hit_threshold(groups: list[OGroup], share: float = 0.2) -> int:
    orders = sorted((d.orders for g in groups for d in g.designs if d.orders > 0), reverse=True)
    return orders[max(0, int(len(orders) * share) - 1)] if orders else 1


def quality(groups: list[OGroup]) -> dict:
    glued = [g for g in groups if g.kind in ("склейка", "малая", "один дизайн")]
    big = [g for g in groups if g.kind == "склейка"]
    singles = [g for g in groups if g.kind == "одиночка"]
    n_designs = sum(len(g.designs) for g in groups)
    sizes = [len(g.designs) for g in big]
    thr = hit_threshold(groups)
    both = sum(1 for g in big if any(d.orders >= thr for d in g.designs) and any(d.orders == 0 for d in g.designs))
    per = defaultdict(list)
    for g in big:
        per[(g.account, g.brand, g.typ)].append(sum(d.orders for d in g.designs))
    cv = [st.pstdev(v) / (sum(v) / len(v)) for v in per.values() if len(v) > 1 and sum(v)]
    tot = ok = 0
    for g in glued:
        if g.typ not in DEFAULT:
            continue
        branch = g.leaf.split(" › ")[0]
        for d in g.designs:
            for f, n in d.features.items():
                tot += n
                ok += n if f == branch else 0
    return {
        "дизайнов": n_designs,
        "склеек": len(big), "малых склеек": sum(1 for g in groups if g.kind == "малая"),
        "склеек из одного дизайна (его SKU вместе)": sum(1 for g in groups if g.kind == "один дизайн"),
        "одиночек (не склеить)": len(singles),
        "дизайнов в склейках": sum(len(g.designs) for g in glued),
        "дизайнов не склеено": sum(len(g.designs) for g in singles),
        "склеек из 6–8 дизайнов": sum(1 for s in sizes if s <= 8),
        "дизайнов в склейке: мин / медиана / макс": f"{min(sizes)} / {st.median(sizes)} / {max(sizes)}" if sizes else "—",
        "доля склеек с хитом и неликвидом": round(both / len(big), 3) if big else 0,
        "разброс силы склеек (CV)": round(st.mean(cv), 2) if cv else 0,
        "чистота по признаку": round(ok / tot, 3) if tot else 1,
        "порог хита (заказов за 28 дней)": thr,
    }


def churn(prev_state: dict, cur_state: dict) -> dict:
    """Доля дизайнов, сменивших склейку между двумя запусками (только дизайны, бывшие в обоих)."""
    a, b = prev_state["assign"], cur_state["assign"]
    common = set(a) & set(b)
    moved = [k for k in common if a[k] != b[k]]
    leaf_moved = [k for k in moved if a[k].rsplit("#", 1)[0] != b[k].rsplit("#", 1)[0]]
    return {
        "дизайнов в обоих днях": len(common), "новых": len(set(b) - set(a)), "выбыло": len(set(a) - set(b)),
        "сменили склейку": len(moved), "доля": round(len(moved) / len(common), 4) if common else 0,
        "из них сменили ветку дерева": len(leaf_moved),
    }
