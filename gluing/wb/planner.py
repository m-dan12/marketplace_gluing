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
from gluing.ozon.sizes import lead_of
from gluing.rules import Context, assign
from gluing.wb import cpsat

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
    mixed: bool = False                  # в группе несколько размеров: хвосты размеров или бренд «вся категория вместе»
    kind: str = "glue"                   # glue | small_glue (меньше min_group) | single_design (одна карточка)


@dataclass
class WbBuild:
    groups: list[WbSet]
    moved: Counter                        # «ветка → куда» для малых веток
    notes: list[str]
    separated: set[str] | None = None     # размеры, отделённые в свою группу (состояние гистерезиса на завтра)


def size_key(c: Card, profile: dict) -> str:
    r = profile["rules"]
    if c.category == "Наволочки" and c.key == r.get("hotel_pillowcase_key"):
        return r["hotel_pillowcase_to"]               # отель приклеивается к обычному размеру
    return c.key


def _fallback_rule(bid: str, cat: str, key: str) -> tuple[str, str]:
    if bid == "ruffle":
        return "ruffle", "Рюши — " + ("комплекты" if cat == "Постельное белье" else "наволочки")
    return f"key:{key}", f"Ключ {key}"


def _key_nums(cards: list[Card]) -> tuple:
    return min((tuple(int(n) for n in k.key_nums) or (10**9,)) for k in cards)


def _distance(a: tuple, b: tuple) -> int:
    n = max(len(a), len(b))
    a, b = a + (0,) * (n - len(a)), b + (0,) * (n - len(b))
    return sum(abs(x - y) for x, y in zip(a, b))


def branch_id(bid: str, cat: str, br: str) -> str:
    return "ветка|" + "|".join((bid, cat, br))


def size_id(key_: tuple) -> str:
    return "|".join((key_[0], *key_[2:]))


def _lead(cat: str, br: str, key: str, r: dict) -> str:
    """Размерность КПБ для разделения пулов хвостов; у остальных категорий и у брендов lead_mix_brands пусто (размеры можно смешивать)."""
    if cat not in r.get("lead_types", []) or br in r.get("lead_mix_brands", []):
        return ""
    return lead_of(key.split("+")[0])


def merge_tails(sets: dict[tuple, list[Card]], profile: dict, prev_sizes: frozenset[str] | set[str] = frozenset(),
                use_solver: bool = False) -> set[str]:
    """Размер — такой же этап деления, как остальные: размер отделяется в свою группу при >= size_split_up карточек, а если вчера
    был отделён, удерживается, пока карточек >= size_merge_down (гистерезис, как у Озона). Остальные размеры — хвосты: они объединяются
    с соседними размерами той же ветки, категории и бренда; остаток меньше min_group присоединяется к ближайшему размеру, где есть
    место. Ромбы и т.п. (`no_mix_families`) и ветки, не делящиеся по размеру, не затрагиваются. Меняет sets на месте;
    возвращает идентификаторы отделённых размеров (состояние для завтрашнего запуска)."""
    lim, r = profile["limits"], profile["rules"]
    work, min_group = lim.get("work_group", lim["max_group"]), lim["min_group"]
    up = r.get("size_split_up", min_group)
    down = r.get("size_merge_down", up)
    skip = set(r.get("no_mix_families", ["rhomb"])) | {"carnival"}
    mix = set(r.get("mix_sizes_bids", []))
    separated: set[str] = set()
    use_solver = use_solver and cpsat.available()
    pools: dict[tuple, list[tuple]] = defaultdict(list)
    for key_, mem in sets.items():
        bid, rule, cat, br, key = key_
        fam = bid.split(":")[0]
        if fam in skip or fam in mix:
            continue
        n = len(mem)
        if n >= up or (size_id(key_) in prev_sizes and n >= down):
            separated.add(size_id(key_))
        else:
            pools[(fam, cat, br, _lead(cat, br, key, r))].append(key_)
    for (fam, cat, br, _), keys in pools.items():
        keys.sort(key=lambda k: _key_nums(sets[k]))
        bins: list[list[tuple]] = [[]]
        size = 0
        for k in keys:
            if bins[-1] and size + len(sets[k]) > work:
                bins.append([])
                size = 0
            bins[-1].append(k)
            size += len(sets[k])
        if len(bins) > 1 and sum(len(sets[k]) for k in bins[-1]) < min_group and                 sum(len(sets[k]) for b in bins[-2:] for k in b) <= work:
            bins[-2] += bins.pop()
        if use_solver and len(keys) > 1:                         # CP-SAT: те же хвосты, но разброс размеров в группе — в целевой функции
            solved = cpsat.solve_tails([(k[4], sets[k]) for k in keys], profile, [[(k[4], len(sets[k])) for k in b] for b in bins])
            if solved:
                first = keys[0]
                for k in keys:
                    del sets[k]
                for g in solved:
                    labels = [lab for lab, _ in g]
                    key = "+".join(labels)
                    sets[(f"{fam}:tails", f"{first[1]} — хвосты размеров", cat, br, key)] = [c for _, cs in g for c in cs]
                continue
        for b in bins:
            if len(b) > 1:
                merged = [c for k in b for c in sets.pop(k)]
                first = b[0]
                sets[(f"{fam}:tails", f"{first[1]} — хвосты размеров", cat, br, "+".join(k[4] for k in b))] = merged
    for key_, mem in list(sets.items()):                         # остаток: присоединяем к ближайшему размеру с местом
        bid, rule, cat, br, key = key_
        fam = bid.split(":")[0]
        if not (len(mem) < min_group and fam not in skip and fam not in mix):
            continue
        lead = _lead(cat, br, key, r)
        hosts = [k for k, m in sets.items() if k != key_ and k[2:4] == (cat, br) and k[0].split(":")[0] == fam.split(":")[0]
                 and _lead(cat, br, k[4], r) == lead
                 and k[0].split(":")[0] not in mix and len(m) >= min_group and len(m) + len(mem) <= work]
        if hosts:
            host = min(hosts, key=lambda k: (_distance(_key_nums(sets[k]), _key_nums(mem)), k))
            merged = sets.pop(host) + sets.pop(key_)
            sets[(host[0], host[1], cat, br, host[4] + "+" + key)] = merged
    return separated


def build_wb(cards: list[Card], live: dict[int, int], imt_members: dict[int, set[int]], profile: dict, ctx: Context,
             prev_sizes: frozenset[str] | set[str] = frozenset(), solver: str | None = None) -> WbBuild:
    """cards — активные карточки с остатком (бренд по префиксу, ключ не пустой); live — {nm_id: текущий imtID};
    imt_members — {imtID: все nm_id в склейке на WB, в том числе неактивные и вне плана}."""
    lim = profile["limits"]
    hard = lim["max_group"]
    work = lim.get("work_group", hard)
    min_group = lim["min_group"]
    order = profile["rules"]["order"]
    mix_bids = set(profile["rules"].get("mix_sizes_bids", []))     # ветки, не делящиеся по размеру («вся категория вместе»)
    sets: dict[tuple, list[Card]] = defaultdict(list)
    for k in cards:
        b = assign(k, ctx, order)
        sets[(b.bid, b.rule, k.category, k.brand, "" if b.bid.split(":")[0] in mix_bids else size_key(k, profile))].append(k)

    moved: Counter = Counter()
    rl = profile["rules"]
    up_b, down_b, feat_min = rl.get("branch_split_up", 10), rl.get("branch_merge_down", 8), rl.get("feature_min_designs", 2)
    prev = set(prev_sizes)
    kept: set[str] = set()                                             # детские и эко, отделённые в свою ветку (гистерезис на завтра)
    changed = True
    while changed:                                                     # ветка решается целиком, как на Озоне; ушедшая уходит в запасную
        changed = False
        branches: dict[tuple, list[Card]] = defaultdict(list)
        for (bid, rule, cat, br, key), mem in sets.items():
            if bid in FALL and key:                                    # key пуст у веток без деления по размеру: запасной ключ не из чего строить
                branches[(bid, rule, cat, br)] += mem
        for (bid, rule, cat, br), mem in sorted(branches.items()):
            n = len({k.design for k in mem})
            bkey = branch_id(bid, cat, br)
            if bid in ("eco", "child"):                                # детские и эко: отделяются при >= branch_split_up дизайнов, удерживаются >= branch_merge_down
                if n >= (down_b if bkey in prev else up_b):
                    kept.add(bkey)
                    continue
            elif n >= feat_min:                                        # признак: определяет ветку, порог лишь отсекает ветку из одного дизайна
                continue
            nb = FALL[bid]
            for key_ in [k for k in sets if k[:4] == (bid, rule, cat, br) and k[4]]:
                key = key_[4]
                nbid, nrule = _fallback_rule(nb, cat, key) if nb else (f"key:{key}", f"Ключ {key}")
                tgt = next((kk for kk in sets if kk[0] == nbid and kk[2:] == (cat, br, key)), (nbid, nrule, cat, br, key))
                sets.setdefault(tgt, [])
                part = sets.pop(key_)
                sets[tgt] = sets[tgt] + part
                moved[f"{rule}->{tgt[1]}"] += len(part)
            changed = True
            break

    sv = profile.get("solver", {})
    mode = solver or sv.get("mode", "greedy")                           # "cpsat" | "greedy"; нет ortools или он не работает: жадный
    use_solver = mode == "cpsat" and cpsat.available()
    separated = merge_tails(sets, profile, prev_sizes, use_solver) if profile["rules"].get("merge_tails", False) else None

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
        if use_solver and len(slots) >= 2 and not grown:                # CP-SAT: раскладка по частям с липкостью к живым склейкам
            room = [work - residual(s) if s else work for s in slots]
            asg = cpsat.solve_parts(mem, room, [slots.index(live.get(k.nm_id)) if live.get(k.nm_id) in slots else None for k in mem],
                                    min_group, sv.get("weights_parts"), sv.get("time_limit", 10))
            if asg is not None:
                where = {k.nm_id: j for k, j in zip(mem, asg)}
        solved_parts = bool(where)
        for k in sorted(mem, key=lambda k: (-k.stock_total, k.article)):
            if solved_parts:
                break
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
                mixed = len({size_key(c, profile) for c in part}) > 1
                out.append(WbSet(imt, bid, rule, cat, br, key, i + 1, part, grown=grown, kind=kind, mixed=mixed))
    return WbBuild(out, moved, notes, (separated or set()) | kept if separated is not None or kept else None)
