"""Близнецы: артикулы одной группы с одинаковыми значениями всех вариативных атрибутов («аспектов») Озона.

Озон не склеит такие товары в одну модель. Подпись варианта = значения атрибутов категории с is_aspect = true.
"""
from __future__ import annotations

from collections import defaultdict


def load_aspects(conn) -> dict[tuple[int, int], dict[int, str]]:
    """(category_id, type_id) -> {attr_id: имя} только для вариативных атрибутов."""
    out: dict[tuple[int, int], dict[int, str]] = defaultdict(dict)
    with conn.cursor() as cur:
        cur.execute("select category_id, type_id, attr_id, name from gluing.category_attr where is_aspect")
        for c, t, a, n in cur.fetchall():
            out[(c, t)][a] = n
    return out


def load_attrs(conn) -> dict[tuple[str, str], dict]:
    """(account, offer_id) -> {category_id, type_id, name, attrs}."""
    with conn.cursor() as cur:
        cur.execute("select account, offer_id, category_id, type_id, name, attrs, product_id, brand_id from gluing.sku_attr")
        return {(a, o): {"cat": (c, t), "name": n, "attrs": at, "product_id": pid, "brand_id": bid}
                for a, o, c, t, n, at, pid, bid in cur.fetchall()}


def signature(info: dict, aspects: dict[tuple[int, int], dict[int, str]]) -> tuple:
    asp = aspects.get(info["cat"], {})
    return tuple((aid, tuple(info["attrs"].get(str(aid), []))) for aid in sorted(asp))


def find_twins(groups: dict[str, list[tuple[str, str]]], attrs: dict, aspects: dict) -> list[dict]:
    """groups: {group_id: [(account, offer_id)]} -> список {group_id, signature, offers} для групп с одинаковой подписью."""
    out = []
    for gid, members in groups.items():
        by: dict[tuple, list[str]] = defaultdict(list)
        for key in members:
            info = attrs.get(key)
            if info is None:
                continue
            by[signature(info, aspects)].append(key[1])
        for sig, offers in by.items():
            if len(offers) > 1:
                out.append({"group_id": gid, "signature": sig, "offers": sorted(offers)})
    return out


def aspect_names(conn) -> dict[int, str]:
    with conn.cursor() as cur:
        cur.execute("select attr_id, max(name) from gluing.category_attr group by 1")
        return dict(cur.fetchall())


# Атрибуты, различия в которых не годятся для названия цвета (служебные, длинные тексты, идентификаторы).
SKIP_ATTRS = {9048, 10097, 10096, 4191, 4180, 22336, 23171}        # модель, цвет, аннотация, название, ключевые слова, хештеги
SKIP_NAMES = ("аннотация", "название", "хештег", "ключев", "описание", "ссылка", "видео", "рич", "изображен", "штрихкод", "артикул")
MAX_TAIL = 30


def distinguishing_value(acc: str, first: list[str], other: list[str], attrs: dict, aspects: dict, names: dict[int, str]) -> tuple[str, str]:
    """Значение атрибута, которым дизайн `other` отличается от дизайна `first` (не вариативный и не служебный). -> (значение, имя атрибута)."""
    sample = attrs.get((acc, first[0])) or attrs.get((acc, other[0]))
    if not sample:
        return "", ""
    asp = set(aspects.get(sample["cat"], {}))
    ids = sorted({int(a) for o in first + other for a in (attrs.get((acc, o)) or {}).get("attrs", {})})
    for aid in ids:
        name = names.get(aid, "")
        if aid in asp or aid in SKIP_ATTRS or any(s in name.lower() for s in SKIP_NAMES):
            continue
        va = {tuple((attrs.get((acc, o)) or {}).get("attrs", {}).get(str(aid), [])) for o in first}
        vb = {tuple((attrs.get((acc, o)) or {}).get("attrs", {}).get(str(aid), [])) for o in other}
        if len(va) == 1 and len(vb) == 1 and va != vb and () not in vb:
            text = "/".join(next(iter(vb)))
            if 0 < len(text) <= MAX_TAIL and any(ch.isalpha() for ch in text):
                return text, name
    return "", ""


def live_models(attrs: dict, attr_id: int = 9048) -> dict[str, str]:
    """{offer_id: живое «Название модели»} из снимка атрибутов (артикулы разных кабинетов не пересекаются)."""
    out = {}
    for (acc, offer), info in attrs.items():
        v = (info["attrs"].get(str(attr_id)) or [""])[0]
        if v:
            out[offer] = v
    return out
