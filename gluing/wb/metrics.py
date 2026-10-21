"""Метрики качества плана WB (одни и те же для жадного режима и CP-SAT)."""
from __future__ import annotations

import statistics
from collections import Counter

from gluing.wb.planner import WbBuild, size_key


def quality(res: WbBuild, live: dict[int, int], profile: dict) -> dict:
    gs = res.groups
    n = sum(len(g.cards) for g in gs)
    kinds = Counter(g.kind for g in gs)
    cards_k: Counter = Counter()
    for g in gs:
        cards_k[g.kind] += len(g.cards)
    mixed = [g for g in gs if g.mixed]
    distinct = [len({size_key(c, profile) for c in g.cards}) for g in mixed]
    wide = [g for g, d in zip(mixed, distinct) if d > 5]
    stay = sum(1 for g in gs for c in g.cards if g.imt_id is not None and live.get(c.nm_id) == g.imt_id)
    glue = [len(g.cards) for g in gs if g.kind == "glue"]
    return {
        "групп": len(gs), "склеек": kinds["glue"], "малых": kinds["small_glue"], "одиночек": kinds["single_design"],
        "карточек в склейках, %": round(100 * cards_k["glue"] / n, 1) if n else 0,
        "размер склейки (медиана)": statistics.median(glue) if glue else 0,
        "смешанных групп": len(mixed), "карточек в смешанных, %": round(100 * sum(len(g.cards) for g in mixed) / n, 1) if n else 0,
        "размеров в смешанной (среднее)": round(sum(distinct) / len(distinct), 2) if distinct else 0,
        "смешанных шире 5 размеров": len(wide),
        "осталось в своей склейке, %": round(100 * stay / n, 1) if n else 0,
    }
