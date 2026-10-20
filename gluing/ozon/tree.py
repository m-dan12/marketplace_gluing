"""Дерево разбиений Озона: бренд → тип → признак → детские → эко → размер группы.

Бренд и тип делят жёстко (Озон склеивает только один тип и бренд). Признак, детские и эко отделяются по порогу
с гистерезисом: отделяем при ≥ split_up дизайнов, сливаем обратно только при < merge_down. Что не отделилось,
остаётся в родителе (признаки уходят по цепочке запасных веток). Дизайн не делится между склейками.
Если ветка больше max_group, делим на равные части, но дизайны «липнут» к вчерашней группе.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from ..lists import child_designs, eco_designs
from ..parsing import design_candidates
from .features import Rules
from . import parts_solver
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


def _clash(a: Design, b: Design) -> bool:
    return bool(a.sigs & b.sigs)


def _clashes(part: list[Design]) -> list[tuple[Design, Design]]:
    return [(a, b) for i, a in enumerate(part) for b in part[i + 1:] if _clash(a, b)]


def _separate_twins(parts: dict[int, list[Design]], mx: int, mn: int) -> list[tuple[Design, Design]]:
    """Разносит по группам дизайны с одинаковой подписью варианта (Озон не склеит близнецов в одну модель).

    Сначала переносит в группу без конфликта, где есть место; если мест нет — меняет местами с подходящим дизайном.
    Возвращает пары, которые развести не удалось."""
    stuck: list[tuple[Design, Design]] = []
    skip: set[tuple[str, str]] = set()
    for _ in range(4 * sum(len(p) for p in parts.values()) + 1):
        todo = None
        for i, part in parts.items():
            for a, b in _clashes(part):
                if (a.uid, b.uid) not in skip:
                    todo = (i, a, b)
                    break
            if todo:
                break
        if todo is None:
            break
        i, a, b = todo
        mover = min((a, b), key=lambda d: (d.orders, d.design))
        rest_i = [d for d in parts[i] if d is not mover]
        done = False
        for j in sorted(parts, key=lambda k: (len(parts[k]), k)):
            if j == i or any(_clash(mover, d) for d in parts[j]):
                continue
            if len(parts[j]) < mx and len(parts[i]) > mn:
                parts[i], parts[j] = rest_i, parts[j] + [mover]
                done = True
                break
            for x in sorted(parts[j], key=lambda d: (d.orders, d.design)):       # обмен
                if not any(_clash(x, d) for d in rest_i) and not any(_clash(mover, d) for d in parts[j] if d is not x):
                    parts[i] = rest_i + [x]
                    parts[j] = [d for d in parts[j] if d is not x] + [mover]
                    done = True
                    break
            if done:
                break
        if not done:
            skip.add((a.uid, b.uid))
            stuck.append((a, b))
    return stuck


def _merge_same_design(ds: list[Design]) -> tuple[list[Design], dict[int, list[str]]]:
    """Части одного дизайна, попавшие в одну ветку, считаются одним дизайном.

    Возвращает (дизайны, {id(дизайна): uid исходных частей}) — по uid ведётся состояние «липкого» назначения."""
    by: dict[tuple[str, str], list[Design]] = {}
    for d in ds:
        by.setdefault((d.design, d.size), []).append(d)
    out: list[Design] = []
    uids: dict[int, list[str]] = {}
    for parts in by.values():
        if len(parts) == 1:
            m = parts[0]
        else:
            f = parts[0]
            m = Design(account=f.account, brand=f.brand, typ=f.typ, design=f.design, digits=f.digits,
                       skus=[s for p in parts for s in p.skus], features=sum((p.features for p in parts), Counter()),
                       feature=f.feature, split=f.split, size=f.size,
                       sigs=frozenset().union(*(p.sigs for p in parts)))
        out.append(m)
        uids[id(m)] = [p.uid for p in parts]
    return out, uids


def build(designs: list[Design], profile: dict, prev: dict | None = None):
    """Возвращает (группы, журнал решений, состояние для завтрашнего запуска)."""
    rules = Rules.from_profile(profile)
    BED, DEFAULT, FALLBACK = rules.bed, rules.default, rules.fallback
    sv = profile.get("solver", {})
    lim, hy = profile["limits"], profile["hysteresis"]
    mx, mn, small_min = lim["max_group"], lim["min_group"], lim["small_group_min"]
    prev_dec = (prev or {}).get("decisions", {})
    prev_asg = (prev or {}).get("assign", {})
    eco, child = eco_designs(), child_designs()
    hard_types = set(profile.get("hard_split", {}).get("types", []))     # детские и эко не смешиваются с обычными ни при каком пороге
    decisions: dict[str, bool] = {}
    log: list[dict] = []
    groups: list[OGroup] = []
    assign: dict[str, str] = {}

    fh = profile.get("features", {})
    feat_up, feat_down = fh.get("split_up", hy["split_up"]), fh.get("merge_down", hy["merge_down"])
    feat_free = "split_up" in fh                          # признаки не требуют жизнеспособного остатка обычной ветки

    def want(key: str, n: int, feat: bool = False) -> bool:
        """Отделять ли ветку: при ≥ split_up, а если вчера была отделена — пока ≥ merge_down."""
        up, down = (feat_up, feat_down) if feat else (hy["split_up"], hy["merge_down"])
        thr = down if prev_dec.get(key) else up
        ok = n >= thr
        decisions[key] = ok
        return ok

    def note(node, level, name, n, result):
        log.append({"node": node, "level": level, "name": name, "n": n, "result": result})

    def finalize(acc, brand, typ, leaf, ds):
        ds, origin = _merge_same_design(ds)
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
            k = math.ceil(n / mx)
            if k == 1 and n >= 2 * mn and _clashes(ds):    # близнецов можно развести только по двум группам
                k = 2
            parts = None
            if k >= 2 and sv.get("parts", "greedy") == "cpsat":      # решатель; нет решения или нет ortools: жадная раскладка
                parts = parts_solver.solve_parts(ds, k, prefix, prev_asg, mx, mn, sv.get("weights"), sv.get("time_limit", 10))
            parts = parts or _assign_parts(ds, k, prefix, prev_asg, mx, mn)
        for a, b in _separate_twins(parts, mx, mn) if len(ds) > 1 else []:
            note(f"{acc}|{brand}|{typ}", "близнецы", f"{a.design} ~ {b.design}", len(ds), "в одной группе, развести нельзя")
        for idx, part in parts.items():
            groups.append(OGroup(key=f"{prefix}#{idx}", account=acc, brand=brand, typ=typ, leaf=leaf, idx=idx,
                                 designs=part, kind=kind))
            for d in part:
                for uid in origin[id(d)]:
                    assign[uid] = f"{prefix}#{idx}"

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
                if not want(f"{node}|признак:{f}", len(members[f]), feat=True):
                    tgt = fb.get(f, default)
                    note(node, "признак", f, len(members[f]), f"мало → в «{tgt}»")
                    members[tgt].extend(members.pop(f))
            sep = {f: v for f, v in members.items() if f != default}
            rest = members.get(default, [])
            need = small_min if feat_free else mn                 # остаток обычной ветки должен быть жизнеспособным
            while sep and 0 < len(rest) < need:
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
            hard = typ in hard_types
            kids = [d for d in rest2 if not d.size and _in(d, child)]    # у типов с размерностью «детское» определяет размер (ключ 1/2), не список дизайнов
            others = [d for d in rest2 if d not in kids]
            if kids and (hard or (want(f"{node}|{bname}|детские", len(kids)) and (len(others) >= mn or not others))):
                note(node, f"детские в «{bname}»", "", len(kids), "отделены")
                leaves.append((f"{bname} › Детские", kids))
                rest2 = others
            elif kids:
                note(node, f"детские в «{bname}»", "", len(kids), "остались в ветке")
            ecos = [d for d in rest2 if _in(d, eco)]
            others = [d for d in rest2 if d not in ecos]
            if ecos and (hard or (want(f"{node}|{bname}|эко", len(ecos)) and (len(others) >= mn or not others))):
                note(node, f"эко в «{bname}»", "", len(ecos), "отделены")
                leaves.append((f"{bname} › Эко", ecos))
                rest2 = others
            elif ecos:
                note(node, f"эко в «{bname}»", "", len(ecos), "остались в ветке")
            if rest2:
                leaves.append((bname, rest2))
            for leaf, lds in leaves:
                by_size: dict[str, list[Design]] = defaultdict(list)
                for d in lds:
                    by_size[d.size].append(d)
                if len(by_size) > 1 or "" not in by_size:     # КПБ: у каждой размерности своя склейка
                    for sz in sorted(by_size):
                        note(node, f"размерность в «{leaf}»", sz, len(by_size[sz]), "отдельная склейка")
                        finalize(acc, brand, typ, f"{leaf} › {sz}", by_size[sz])
                else:
                    finalize(acc, brand, typ, leaf, lds)

    state = {"decisions": decisions, "assign": assign}
    return groups, log, state
