"""Дерево разбиений Озона: бренд → тип → признак → детские → эко → размер группы.

Бренд и тип делят жёстко (Озон склеивает только один тип и бренд). Признак, детские и эко отделяются по порогу
с гистерезисом: отделяем при ≥ split_up дизайнов, сливаем обратно только при < merge_down. Что не отделилось,
остаётся в родителе (признаки уходят по цепочке запасных веток). Дизайн не делится между склейками.
Если ветка больше max_group, делим на равные части, но дизайны «липнут» к вчерашней группе.
"""
from __future__ import annotations

import math
from collections import defaultdict

from ..lists import child_designs, eco_designs
from ..parsing import design_candidates
from .features import BED, DEFAULT, FALLBACK
from .model import Design, OGroup


def _in(design: Design, lst: frozenset[str]) -> bool:
    return any(c in lst for c in design_candidates(design.digits))


def _assign_parts(ds: list[Design], k: int, prefix: str, prev_asg: dict, mx: int, mn: int) -> dict[int, list[Design]]:
    """Раскладка дизайнов ветки по k группам: прежнее место сохраняется, новые идут в самую малую группу."""
    buckets: dict[int, list[Design]] = defaultdict(list)
    free: list[Design] = []
    for d in ds:
        p = prev_asg.get(d.uid)
        if p and p.rsplit("#", 1)[0] == prefix:
            buckets[int(p.rsplit("#", 1)[1])].append(d)
        else:
            free.append(d)
    idxs = sorted(buckets)
    nxt = (idxs[-1] + 1) if idxs else 1
    while len(idxs) < k:                                  # нужна ещё группа
        idxs.append(nxt)
        buckets[nxt] = []
        nxt += 1
    while len(idxs) > k:                                  # групп больше, чем нужно: растворяем самую малую
        smallest = min(idxs, key=lambda i: (len(buckets[i]), -i))
        free += buckets.pop(smallest)
        idxs.remove(smallest)
    for d in sorted(free, key=lambda d: (-d.orders, d.design)):
        tgt = min(idxs, key=lambda i: (len(buckets[i]), i))
        buckets[tgt].append(d)
    # границы 6..18: лишнее переезжает из самой большой в самую малую группу (по наименьшим заказам)
    for _ in range(len(ds)):
        big = max(idxs, key=lambda i: len(buckets[i]))
        small = min(idxs, key=lambda i: len(buckets[i]))
        if len(buckets[big]) > mx or (len(buckets[small]) < mn and len(buckets[big]) > mn):
            moved = min(buckets[big], key=lambda d: (d.orders, d.design))
            buckets[big].remove(moved)
            buckets[small].append(moved)
        else:
            break
    return {i: buckets[i] for i in idxs}


def build(designs: list[Design], profile: dict, prev: dict | None = None):
    """Возвращает (группы, журнал решений, состояние для завтрашнего запуска)."""
    lim, hy = profile["limits"], profile["hysteresis"]
    mx, mn, small_min = lim["max_group"], lim["min_group"], lim["small_group_min"]
    prev_dec = (prev or {}).get("decisions", {})
    prev_asg = (prev or {}).get("assign", {})
    eco, child = eco_designs(), child_designs()
    decisions: dict[str, bool] = {}
    log: list[dict] = []
    groups: list[OGroup] = []
    assign: dict[str, str] = {}

    def want(key: str, n: int) -> bool:
        """Отделять ли ветку: при ≥ split_up, а если вчера была отделена — пока ≥ merge_down."""
        thr = hy["merge_down"] if prev_dec.get(key) else hy["split_up"]
        ok = n >= thr
        decisions[key] = ok
        return ok

    def note(node, level, name, n, result):
        log.append({"node": node, "level": level, "name": name, "n": n, "result": result})

    def finalize(acc, brand, typ, leaf, ds):
        n = len(ds)
        prefix = f"{acc}|{brand}|{typ}|{leaf}"
        if n < mn:
            if n >= small_min:
                kind = "малая"
            else:                                         # один дизайн: его SKU всё равно склеиваются между собой
                kind = "один дизайн" if sum(len(d.skus) for d in ds) > 1 else "одиночка"
            parts = {1: ds}
        else:
            kind = "склейка"
            parts = _assign_parts(ds, math.ceil(n / mx), prefix, prev_asg, mx, mn)
        for idx, part in parts.items():
            groups.append(OGroup(key=f"{prefix}#{idx}", account=acc, brand=brand, typ=typ, leaf=leaf, idx=idx,
                                 designs=part, kind=kind))
            for d in part:
                assign[d.uid] = f"{prefix}#{idx}"

    nodes: dict[tuple, list[Design]] = defaultdict(list)
    for d in designs:
        nodes[(d.account, d.brand, d.typ)].append(d)

    for (acc, brand, typ), ds in sorted(nodes.items()):
        node = f"{acc}|{brand}|{typ}"
        branches: dict[str, list[Design]] = {"—": ds}
        if typ in DEFAULT:
            default, fb = DEFAULT[typ], FALLBACK.get(typ, {})
            members: dict[str, list[Design]] = defaultdict(list)
            for d in ds:
                members[d.feature].append(d)

            def depth(f):
                n = 0
                while f in fb:
                    f, n = fb[f], n + 1
                return n

            for f in sorted((f for f in members if f != default), key=lambda f: -depth(f)):
                if not want(f"{node}|признак:{f}", len(members[f])):
                    tgt = fb.get(f, default)
                    note(node, "признак", f, len(members[f]), f"мало → в «{tgt}»")
                    members[tgt].extend(members.pop(f))
            sep = {f: v for f, v in members.items() if f != default}
            rest = members.get(default, [])
            while sep and 0 < len(rest) < mn:             # остаток обычной ветки должен быть жизнеспособным
                f = min(sep, key=lambda x: len(sep[x]))
                note(node, "признак", f, len(sep[f]), "возвращён: остаток обычных меньше минимума")
                rest = rest + sep.pop(f)
            for f, v in sep.items():
                note(node, "признак", f, len(v), "отделён")
            branches = ({default: rest} if rest else {}) | sep

        for bname, bds in branches.items():
            if typ not in BED:
                finalize(acc, brand, typ, bname, bds)
                continue
            leaves = []
            rest2 = bds
            kids = [d for d in rest2 if _in(d, child)]
            others = [d for d in rest2 if d not in kids]
            if kids and want(f"{node}|{bname}|детские", len(kids)) and (len(others) >= mn or not others):
                note(node, f"детские в «{bname}»", "", len(kids), "отделены")
                leaves.append((f"{bname} › Детские", kids))
                rest2 = others
            elif kids:
                note(node, f"детские в «{bname}»", "", len(kids), "остались в ветке")
            ecos = [d for d in rest2 if _in(d, eco)]
            others = [d for d in rest2 if d not in ecos]
            if ecos and want(f"{node}|{bname}|эко", len(ecos)) and (len(others) >= mn or not others):
                note(node, f"эко в «{bname}»", "", len(ecos), "отделены")
                leaves.append((f"{bname} › Эко", ecos))
                rest2 = others
            elif ecos:
                note(node, f"эко в «{bname}»", "", len(ecos), "остались в ветке")
            if rest2:
                leaves.append((bname, rest2))
            for leaf, lds in leaves:
                finalize(acc, brand, typ, leaf, lds)

    state = {"decisions": decisions, "assign": assign}
    return groups, log, state
