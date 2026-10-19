"""Стабильные id групп: сначала вчерашний id той же ветки, потом наследование живого «Названия модели», иначе новый."""
from __future__ import annotations

import hashlib
from collections import Counter

MIN_OVERLAP = 0.5


def new_id(key: str) -> str:
    return "g_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def assign_ids(groups: list[tuple[str, list[str]]], prev_ids: dict[str, str] | None = None,
               live_models: dict[str, str] | None = None, min_overlap: float = MIN_OVERLAP) -> dict[str, str]:
    """groups: [(key, [offer_id...])] -> {key: group_id}.

    live_models — {offer_id: «Название модели» на площадке}. Живые имена раздаются группам по максимальному перекрытию
    (пары «группа — имя» по убыванию числа общих артикулов; имя и группа берутся один раз), чтобы минимум артикулов
    сменили модель. Группа берёт имя, если оно у не менее доли `min_overlap` её артикулов. Реальность важнее прошлого плана.
    prev_ids  — {key: group_id} прошлого плана; если живого имени нет, ключ сохранился, значит id тот же.
    Результат детерминирован: порядок пар фиксирован (число артикулов, затем ключ и имя).
    """
    prev_ids, live_models = prev_ids or {}, live_models or {}
    out: dict[str, str] = {}
    taken: set[str] = set()
    pairs = []
    for key, offers in groups:
        if not offers:
            continue
        for name, c in Counter(live_models[o] for o in offers if live_models.get(o)).items():
            if c / len(offers) >= min_overlap:
                pairs.append((-c, key, name))
    for _, key, name in sorted(pairs):
        if key not in out and name not in taken:
            out[key] = name
            taken.add(name)
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
