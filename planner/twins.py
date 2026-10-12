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
        cur.execute("select account, offer_id, category_id, type_id, name, attrs from gluing.sku_attr")
        return {(a, o): {"cat": (c, t), "name": n, "attrs": at} for a, o, c, t, n, at in cur.fetchall()}


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
