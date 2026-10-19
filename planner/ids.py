"""Стабильные id групп: сначала вчерашний id той же ветки, потом наследование живого «Названия модели», иначе новый."""
from __future__ import annotations

import hashlib
from collections import Counter

MIN_OVERLAP = 0.5


def new_id(key: str) -> str:
    return "g_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def assign_ids(groups: list[tuple[str, list[str]]], prev_ids: dict[str, str] | None = None,
               live_models: dict[str, str] | None = None) -> dict[str, str]:
    """groups: [(key, [offer_id...])] -> {key: group_id}.

    live_models — {offer_id: «Название модели» на площадке}; группа наследует имя, которым уже
    называется не менее половины её артикулов, если имя ещё не занято (реальность важнее прошлого плана).
    prev_ids  — {key: group_id} прошлого плана; если живого имени нет, ключ сохранился, значит id тот же.
    Порядок обработки детерминирован (по убыванию размера, затем по ключу), поэтому результат повторяем.
    """
    prev_ids, live_models = prev_ids or {}, live_models or {}
    out: dict[str, str] = {}
    taken: set[str] = set()
    for key, offers in sorted(groups, key=lambda g: (-len(g[1]), g[0])):      # сначала то, что реально стоит на площадке
        if not offers:
            continue
        cnt = Counter(live_models[o] for o in offers if live_models.get(o))
        for name, c in sorted(cnt.items(), key=lambda x: (-x[1], x[0])):
            if name not in taken and c / len(offers) >= MIN_OVERLAP:
                out[key] = name
                taken.add(name)
                break
    for key, _ in groups:                                   # затем id прошлого плана для той же ветки
        gid = prev_ids.get(key)
        if key not in out and gid and gid not in taken:
            out[key] = gid
            taken.add(gid)
    for key, _ in groups:
        if key not in out:
            gid = new_id(key)
            n = 2
            while gid in taken:                              # коллизия хэша — крайне редкая, но id должен быть уникален
                gid, n = new_id(f"{key}#{n}"), n + 1
            out[key] = gid
            taken.add(gid)
    return out
