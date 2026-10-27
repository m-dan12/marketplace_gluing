"""Рекомендации продавца на WB: для каждой карточки с остатком набираем до 20 артикулов по слотам (свой дизайн, хиты, неликвиды, новинки, ...).

Слоты и их размеры задаёт профиль (раздел [recommend]); не набранный слот уходит в добивку. Одни и те же хиты/новинки/неликвиды не ставим всем подряд:
из окна лучших кандидатов берём тех, кого до сих пор рекомендовали реже всего (ротация).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable

from gluing.lists import eco_designs
from gluing.model import Card
from gluing.parsing import design_candidates


@dataclass
class Result:
    recs: dict[int, list[tuple[int, str]]] = field(default_factory=dict)      # nm_id -> [(рекомендуемый nm_id, слот)]
    short: Counter = field(default_factory=Counter)                            # (группа, слот) -> на сколько не добрали
    used: Counter = field(default_factory=Counter)                             # nm_id -> сколько раз порекомендован


def group_of(c: Card, prof: dict) -> str | None:
    r, rc = prof["rules"], prof["recommend"]
    if c.category in r["bedding_categories"]:
        return "bedding"
    if c.category in rc["decor_categories"]:
        return "decor"
    if c.category == r["fabric_category"]:
        return "fabric"
    if c.category == r["carnival_category"]:
        return "carnival"
    return None


def _is_ruffle(c: Card, prof: dict) -> bool:
    return len(c.key_nums) >= 3 and c.key_nums[2] in prof["rules"]["ruffle_ends"] and c.category in {"Наволочки", "Постельное белье"}


def _is_rhomb(c: Card, prof: dict) -> bool:
    r = prof["rules"]
    return c.category in r.get("rhomb_categories", ["Пододеяльники"]) and bool(c.key_nums) and c.key_nums[0] in r["rhomb_first"]


def _pool_key(c: Card, group: str, prof: dict, eco: frozenset[str]) -> tuple:
    """Кого с кем рекомендуем: у постельного — класс бренда (Мечта только с Мечтой) и эко отдельно от остального."""
    if group != "bedding":
        return (group,)
    rules = prof["rules"]
    cls = next((i for i, g in enumerate(prof["recommend"]["brand_classes"]) if c.brand in g), c.brand)
    is_eco = (c.brand not in rules["no_eco_brands"]) and any(d in eco for d in design_candidates(c.design))
    return (group, cls, is_eco)


class _Pool:
    def __init__(self, cards: list[Card], prof: dict):
        by_orders = sorted(cards, key=lambda c: (-c.orders30, -c.stock_total, c.nm_id))
        self.all = by_orders
        self.top = [c for c in by_orders if c.orders30 > 0]
        self.new = sorted(cards, key=lambda c: -c.nm_id)
        self.dead = sorted(cards, key=lambda c: (c.orders30 / c.stock_total, -c.stock_total, c.nm_id))
        self.ruffle = [c for c in by_orders if _is_ruffle(c, prof)]
        self.rhomb = [c for c in by_orders if _is_rhomb(c, prof)]
        self.by_design: dict[str, list[Card]] = defaultdict(list)
        for c in by_orders:
            self.by_design[c.design].append(c)


def recommend(cards: list[Card], imt: dict[int, int], prof: dict, eco: frozenset[str] | None = None, eligible: set[int] | None = None) -> Result:
    """cards — только карточки с остатком одного кабинета; imt — {nm_id: imtID} (карточки одной склейки друг другу не рекомендуем).
    eligible — какие карточки можно рекомендовать (None: все); рекомендации получают все карточки, в том числе новые, которых WB пока не принимает как рекомендуемые."""
    rc = prof["recommend"]
    total, window = rc["total"], rc["rotation"]
    eco = eco if eco is not None else frozenset()
    pools: dict[tuple, _Pool] = {}
    members: dict[tuple, list[Card]] = defaultdict(list)
    keyed: list[tuple[Card, str, tuple]] = []
    for c in cards:
        g = group_of(c, prof)
        if g is None or c.stock_total <= 0:
            continue
        k = _pool_key(c, g, prof, eco)
        if eligible is None or c.nm_id in eligible:
            members[k].append(c)
        keyed.append((c, g, k))
    for k in {k for _, _, k in keyed}:
        pools[k] = _Pool(members.get(k, []), prof)

    res = Result()
    used = res.used
    pick_from: dict[str, Callable[[_Pool, Card], list[Card]]] = {
        "design": lambda p, c: p.by_design[c.design],
        "top": lambda p, c: p.top, "dead": lambda p, c: p.dead, "new": lambda p, c: p.new,
        "ruffle": lambda p, c: p.ruffle, "rhomb": lambda p, c: p.rhomb,
    }
    for c, g, k in sorted(keyed, key=lambda t: t[0].nm_id):
        pool, spec = pools[k], rc[g]
        mates = imt.get(c.nm_id)
        chosen: dict[int, str] = {}

        def ok(x: Card, other_design: bool) -> bool:
            return (x.nm_id != c.nm_id and x.nm_id not in chosen and not (mates is not None and imt.get(x.nm_id) == mates)
                    and not (other_design and x.design == c.design))

        def take(ranked: list[Card], n: int, slot: str, other_design: bool = True, same_category: bool = False) -> None:
            n = min(n, total - len(chosen))
            if n <= 0:
                return
            cand: list[Card] = []
            for x in ranked:
                if ok(x, other_design) and (not same_category or x.category == c.category):
                    cand.append(x)
                    if len(cand) >= n * window:
                        break
            cand.sort(key=lambda x: used[x.nm_id])                       # стабильно: при равной ротации сохраняется порядок качества
            for x in cand[:n]:
                chosen[x.nm_id] = slot
            if len(cand) < n and slot != "fill":
                res.short[(g, slot)] += n - len(cand)

        for slot, n in spec["slots"].items():
            if slot == "design":
                if spec.get("design_all"):                              # весь дизайн, а если вариантов больше нормы, то больше (но не больше design_cap)
                    n = max(n, min(len(pool.by_design[c.design]), rc["design_cap"]))
                take(pool.by_design[c.design], n, slot, other_design=False)
            elif slot in pick_from:
                take(pick_from[slot](pool, c), n, slot)
        if spec.get("fill_same_category") and len(chosen) < total:
            take(pool.all, total - len(chosen), "fill", same_category=True)
        take(pool.all, total - len(chosen), "fill")
        res.recs[c.nm_id] = list(chosen.items())
        for nm in chosen:
            used[nm] += 1
    return res
