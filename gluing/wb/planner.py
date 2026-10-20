"""Планировщик склеек WB (чистая функция, без БД и сети).

Карточка WB = дизайн + один размер, многоуровневой склейки нет, поэтому в группе один размер (ключ артикула) у разных дизайнов.
Шаги: ветка по правилам проекта → внутри ветки ключ размера → малый ключ уходит по запасной цепочке → части не больше рабочего лимита
`work_group` (поровну) → группа наследует imtID, где уже лежит больше всего её карточек.

Лимиты профиля: `max_group` — жёсткий лимит WB (30), `work_group` — рабочий (26). Рабочий лимит можно превысить до жёсткого,
когда в уже существующей склейке появилось 1–4 новых артикула и делить её на две невыгодно (`n <= max_group` и есть живой imtID с местом).
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from gluing.model import Card
from gluing.rules import Context, assign

FALL = {"eco_ruffle": "ruffle", "ruffle": None, "eco": None, "child": None, "kant": None}   # куда уходит малая ветка


@dataclass
class WbSet:
    """Будущая склейка: imt_id — наследуемая живая склейка, None — новая."""
    imt_id: int | None
    bid: str
    rule: str
    category: str
    brand: str
    size_key: str
    part: int
    cards: list[Card]
    grown: bool = False                  # превысили рабочий лимит, но не жёсткий (допуск роста)
    kind: str = "glue"                   # glue | small_glue (меньше min_group) | single_design (одна карточка)


@dataclass
class WbBuild:
    groups: list[WbSet]
    moved: Counter                        # «ветка → куда» для малых веток
    notes: list[str]


def size_key(c: Card, profile: dict) -> str:
    r = profile["rules"]
    if c.category == "Наволочки" and c.key == r.get("hotel_pillowcase_key"):
        return r["hotel_pillowcase_to"]               # отель приклеивается к обычному размеру
    return c.key


def _fallback_rule(bid: str, cat: str, key: str) -> tuple[str, str]:
    if bid == "ruffle":
        return "ruffle", "Рюши — " + ("комплекты" if cat == "Постельное белье" else "наволочки")
    return f"key:{key}", f"Ключ {key}"


def build_wb(cards: list[Card], live: dict[int, int], imt_members: dict[int, set[int]], profile: dict, ctx: Context) -> WbBuild:
    """cards — активные карточки с остатком (бренд по префиксу, ключ не пустой); live — {nm_id: текущий imtID};
    imt_members — {imtID: все nm_id в склейке на WB, в том числе неактивные и вне плана}."""
    lim = profile["limits"]
    hard = lim["max_group"]
    work = lim.get("work_group", hard)
    min_group = lim["min_group"]
    order = profile["rules"]["order"]
    sets: dict[tuple, list[Card]] = defaultdict(list)
    for k in cards:
        b = assign(k, ctx, order)
        sets[(b.bid, b.rule, k.category, k.brand, size_key(k, profile))].append(k)

    moved: Counter = Counter()
    changed = True
    while changed:                                                     # малая ветка уходит в запасную, пока есть что переносить
        changed = False
        for key_, mem in list(sets.items()):
            bid, rule, cat, br, key = key_
            if bid in FALL and 0 < len(mem) < min_group:
                nb = FALL[bid]
                nbid, nrule = _fallback_rule(nb, cat, key) if nb else (f"key:{key}", f"Ключ {key}")
                tgt = next((kk for kk in sets if kk[0] == nbid and kk[2:] == (cat, br, key)), (nbid, nrule, cat, br, key))
                sets.setdefault(tgt, [])
                sets[tgt] = sets[tgt] + mem
                del sets[key_]
                moved[f"{rule}->{tgt[1]}"] += len(mem)
                changed = True
                break

    planned = {k.nm_id for k in cards}
    members = {i: set(v) for i, v in imt_members.items()}
    used: set[int] = set()
    out: list[WbSet] = []
    notes: list[str] = []

    def residual(imt: int) -> int:
        return len(members.get(imt, set()) - planned)                  # неактивные карточки остаются в склейке и занимают место

    for (bid, rule, cat, br, key), mem in sorted(sets.items(), key=lambda x: (-len(x[1]), x[0])):
        n = len(mem)
        cnt = Counter(live[k.nm_id] for k in mem if live.get(k.nm_id) is not None)
        cand = [i for i, _ in cnt.most_common() if i not in used]
        grown = False
        if work < n <= hard:                                           # допуск роста: одна существующая склейка вместо деления
            one = next((i for i in cand if hard - residual(i) >= n), None)
            if one is not None:
                slots, caps, grown = [one], [n], True
        if not grown:
            slots: list[int | None] = []
            tot = 0
            for imt in cand:
                if tot >= n:
                    break
                c = work - residual(imt)
                if c > 0:
                    slots.append(imt)
                    tot += c
            while tot < n:
                slots.append(None)
                tot += work
            parts = len(slots)
            room = [work - residual(s) if s else work for s in slots]
            base = math.ceil(n / parts)
            caps = [min(r, base) for r in room]
            while sum(caps) < n:
                j = max(range(parts), key=lambda j: room[j] - caps[j])
                caps[j] += 1
        used.update(i for i in slots if i)
        load = [0] * len(slots)
        where: dict[int, int] = {}
        for k in sorted(mem, key=lambda k: (-k.stock_total, k.article)):
            cur = live.get(k.nm_id)
            i = slots.index(cur) if cur in slots else None
            if i is None or load[i] >= caps[i]:
                i = max((j for j in range(len(slots)) if load[j] < caps[j]), key=lambda j: caps[j] - load[j])
            where[k.nm_id] = i
            load[i] += 1
        for i, imt in enumerate(slots):
            part = [k for k in mem if where[k.nm_id] == i]
            if part:
                kind = "glue" if len(part) >= min_group else ("small_glue" if len(part) > 1 else "single_design")
                out.append(WbSet(imt, bid, rule, cat, br, key, i + 1, part, grown=grown, kind=kind))
    return WbBuild(out, moved, notes)
