"""Сборка плана (контракт) из результата дерева разбиений Озона."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

from contract import Check, Decision, Design, Group, Member, Plan, RunInfo
from gluing.ozon.model import OGroup
from planner.ids import assign_ids
from planner.twins import COLOR_NAME_ATTR

KIND = {"склейка": "glue", "малая": "small_glue", "один дизайн": "single_design", "одиночка": "unglued"}


def profile_hash(profile: dict) -> str:
    return hashlib.sha1(json.dumps(profile, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:12]


def check_groups(groups: list[Group], profile: dict) -> Check:
    lim = profile["limits"]
    hard: list[str] = []
    warn: list[str] = []
    for g in groups:
        if g.kind == "unglued":
            continue
        if g.n_designs > lim["max_group"]:
            hard.append(f"{g.group_id}: {g.n_designs} дизайнов, больше {lim['max_group']}")
        if g.kind == "glue" and g.n_designs < lim["min_group"]:
            hard.append(f"{g.group_id}: склейка из {g.n_designs} дизайнов, меньше {lim['min_group']}")
        if g.kind == "small_glue":
            warn.append(f"{g.group_id}: малая группа из {g.n_designs} дизайнов")
    return Check(hard_violations=hard, warnings=warn)


def _color_of(attrs: dict, key: tuple[str, str]) -> str:
    return ((attrs.get(key) or {}).get("attrs", {}).get(str(COLOR_NAME_ATTR)) or [""])[0]


def with_colors(attrs: dict, fixes: dict[tuple[str, str], str]) -> dict:
    """Копия снимка атрибутов с подставленными значениями «Названия цвета» (исходный снимок не меняется)."""
    out = dict(attrs)
    for key, value in fixes.items():
        if key in out:
            out[key] = {**out[key], "attrs": {**out[key]["attrs"], str(COLOR_NAME_ATTR): [value]}}
    return out


def unify_colors(groups: list[OGroup], attrs: dict, aspects: dict) -> tuple[list[dict], dict[tuple[str, str], str]]:
    """Одно «Название цвета» на дизайн: иначе Озон показывает его артикулы в склейке соседними иконками.

    Берётся самое частое значение среди артикулов дизайна (вес: заказы за 28 дней + 1), при равенстве — по алфавиту. Пустые
    значения заполняются. Только для категорий, где «Название цвета» есть среди вариативных характеристик; дизайн без единого
    значения не трогаем (выдумывать название нельзя). -> (записи для плана, {(кабинет, артикул): новое значение})."""
    by_design: dict[tuple[str, str], list] = {}
    for g in groups:
        for d in g.designs:
            by_design.setdefault((g.account, d.design), []).extend(d.skus)
    fixes: dict[tuple[str, str], str] = {}
    records: list[dict] = []
    for (acc, design), skus in sorted(by_design.items()):
        skus = [s for s in skus if (acc, s.offer_id) in attrs and COLOR_NAME_ATTR in aspects.get(attrs[(acc, s.offer_id)]["cat"], {})]
        if len(skus) < 2:
            continue
        weight: Counter = Counter()
        for s in skus:
            v = _color_of(attrs, (acc, s.offer_id))
            if v:
                weight[v] += s.orders28 + 1
        if not weight:
            continue
        best = max(weight.values())
        value = min(v for v, w in weight.items() if w == best)
        changed = {s.offer_id: _color_of(attrs, (acc, s.offer_id)) for s in skus if _color_of(attrs, (acc, s.offer_id)) != value}
        if changed:
            records.append({"account": acc, "design": design, "value": value, "offers": changed})
            for o in changed:
                fixes[(acc, o)] = value
    return records, fixes


def twins_check(groups: list[OGroup], attrs: dict, aspects: dict, names: dict[int, str] | None = None) -> list[dict]:
    """Близнецы в итоговых группах + предложение правки «Названия цвета» (человек подтверждает, на площадку не пишем)."""
    from planner.twins import find_twins
    names = names or {}
    keys = {g.key: [(g.account, s.offer_id) for d in g.designs for s in d.skus] for g in groups if g.kind != "одиночка"}
    design_of = {(g.account, s.offer_id): d.design for g in groups for d in g.designs for s in d.skus}
    offers_of: dict[tuple[str, str], list[str]] = {}
    for g in groups:
        for d in g.designs:
            offers_of.setdefault((g.account, d.design), []).extend(s.offer_id for s in d.skus)

    def whole_design(acc: str, design: str, twinned: list[str]) -> list[str]:
        """Близнецы переименовываются дизайном целиком (все артикулы дизайна), иначе значения цвета внутри дизайна разойдутся."""
        return sorted(set(offers_of.get((acc, design), [])) | set(twinned))
    out = []
    for t in find_twins(keys, attrs, aspects):
        acc = next(g.account for g in groups if g.key == t["group_id"])
        color = next((v[0] for a, v in t["signature"] if names.get(a) == "Название цвета" and v), "")
        by_design: dict[str, list[str]] = {}
        for o in t["offers"]:
            by_design.setdefault(design_of[(acc, o)], []).append(o)
        designs = sorted(by_design)
        color_ids = [a for a, n in names.items() if n == "Название цвета"]
        used = {v for (a, o) in keys[t["group_id"]] for cid in color_ids
                for v in (attrs.get((a, o), {}).get("attrs", {}).get(str(cid)) or [])}
        props, n = [], 1
        from planner.twins import distinguishing_value
        # внутри одного дизайна (два артикула с одинаковыми вариантами) правим каждый артикул, кроме первого
        units = [(d, whole_design(acc, d, by_design[d])) for d in designs[1:]] if len(designs) > 1 else             [(designs[0], [o]) for o in sorted(by_design[designs[0]])[1:]]
        first = by_design[designs[0]] if len(designs) > 1 else sorted(by_design[designs[0]])[:1]
        for d, offs in units:
            tail, source = distinguishing_value(acc, first, offs, attrs, aspects, names)
            new = f"{color}, {tail}".strip(", ") if tail else ""
            if not new or new in used:                  # нет различающегося атрибута или имя занято: порядковый номер
                n += 1
                while f"{color} {n}" in used:
                    n += 1
                new, source = f"{color} {n}".strip(), "порядковый номер"
            used.add(new)
            props.append({"design": d, "offers": offs, "attr": "Название цвета", "new_value": new, "source": source})
        out.append({"group_key": t["group_id"], "color": color, "offers": t["offers"], "designs": designs,
                    "same_design": len(designs) == 1, "proposals": props})
    return out


def _with_twins(check: Check, groups, attrs, aspects, names, ids) -> Check:
    if attrs is None or aspects is None:
        return check
    colors, fixes = unify_colors(groups, attrs, aspects)
    check.colors = colors
    if colors:
        check.warnings.append(f"единое название цвета: {len(colors)} дизайнов, {len(fixes)} артикулов")
    tw = twins_check(groups, with_colors(attrs, fixes), aspects, names)
    for t in tw:
        t["group_id"] = ids.get(t["group_key"], t["group_key"])
    check.twins = tw
    if tw:
        check.warnings.append(f"близнецы: {len(tw)} групп(ы) с одинаковыми вариантами, Озон их не склеит")
    return check


def build_plan(groups: list[OGroup], log: list[dict], state: dict, profile: dict, *, run_id: str,
               profile_name: str = "ozon", engine_version: str = "dev", inputs: dict | None = None,
               platform_ids: dict[tuple[str, str], dict] | None = None,
               prev_ids: dict[str, str] | None = None, live_models: dict[str, str] | None = None,
               mode: str = "shadow", created_at: str | None = None,
               attrs: dict | None = None, aspects: dict | None = None, aspect_names: dict | None = None) -> Plan:
    platform_ids = platform_ids or {}
    ids = assign_ids([(g.key, [s.offer_id for d in g.designs for s in d.skus]) for g in groups], prev_ids, live_models)
    out: list[Group] = []
    unglued: list[dict] = []
    for g in groups:
        kind = KIND[g.kind]
        if kind == "unglued":
            for d in g.designs:
                for s in d.skus:
                    unglued.append({"account": g.account, "offer_id": s.offer_id, "reason": "one_sku_in_type"})
            continue
        gid = ids[g.key]
        out.append(Group(
            group_id=gid, key=g.key, account=g.account, brand=g.brand, type=g.typ, branch=g.leaf, part=g.idx,
            kind=kind, target={"ozon": {"model_name": gid}},
            designs=[Design(design=d.design, feature=d.feature, members=[
                Member(offer_id=s.offer_id, stock=s.stock + s.stock_roll, orders_28d=s.orders28,
                       platform_ids=platform_ids.get((g.account, s.offer_id), {})) for s in d.skus]) for d in g.designs],
            reasons=[f"ветка «{g.leaf}»", f"{len(g.designs)} дизайнов"]))
    return Plan(
        run=RunInfo(run_id=run_id, platform="ozon", engine_version=engine_version, profile_name=profile_name,
                    profile_hash=profile_hash(profile), inputs=inputs or {}, mode=mode,
                    created_at=created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        groups=out, unglued=unglued, checks=_with_twins(check_groups(out, profile), groups, attrs, aspects, aspect_names, ids),
        decisions=[Decision(**x) for x in log], state=state)
