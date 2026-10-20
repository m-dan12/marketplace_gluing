"""Применяющий скрипт Озона, часть 1: разница «план → живое состояние» и предварительные проверки (dry-run).

Ничего не пишет на площадку. Вход: план (contract.Plan) и живое состояние карточек (атрибуты по артикулам).
Выход: список операций, заблокированные группы и причины. Идемпотентность: артикул, у которого значение уже верное, пропускается.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from applier.brands import NO_BRAND, allowed_current, brand_attr_of, brand_ids, target_id
from contract import Plan
from planner.twins import signature

MODEL_NAME_ATTR = 9048          # «Название модели»: единое значение = одна склейка на Озоне
COLOR_NAME_ATTR = 10097         # «Название цвета»
BRAND_ATTR = 85                 # «Бренд»
MAX_DESIGNS = 18


@dataclass
class Op:
    account: str
    offer_id: str
    op_type: str                # set_brand | set_color_name | set_model_name | unglue_model
    attr_id: int
    from_value: str
    to_value: str
    group_id: str
    product_id: int | None = None
    from_id: int | None = None  # dictionary_value_id «было» (бренд); для отката
    to_id: int | None = None    # dictionary_value_id «стало» (бренд)


@dataclass
class Blocked:
    group_id: str
    reasons: list[str]


@dataclass
class DryRun:
    run_id: str
    ops: list[Op] = field(default_factory=list)
    blocked: list[Blocked] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    already_ok: int = 0
    members: int = 0

    def summary(self) -> dict:
        by_type: dict[str, int] = defaultdict(int)
        for o in self.ops:
            by_type[o.op_type] += 1
        return {"run_id": self.run_id, "артикулов в плане": self.members, "уже верно": self.already_ok,
                "операций": len(self.ops), "по типам": dict(by_type),
                "групп заблокировано": len(self.blocked), "предупреждений": len(self.warnings)}


def _first(attrs: dict, attr_id: int) -> str:
    v = attrs.get(str(attr_id)) or []
    return v[0] if v else ""


def dry_run(plan: Plan, live: dict[tuple[str, str], dict], aspects: dict,
            stock: dict[tuple[str, str], int] | None = None, min_total: int = 2,
            brand_map: dict[str, str] | None = None) -> DryRun:
    """live: {(account, offer_id): {"cat": (category_id, type_id), "attrs": {attr_id: [values]}, "product_id": int|None}}

    stock: {(account, offer_id): остаток}. Если задан, артикулы вне плана с остатком меньше min_total, которые всё ещё
    носят имя модели из плана, снимаются с неё (unglue_model: имя модели = собственный артикул, как у несклеенных карточек).

    brand_map: {бренд плана: бренд на Озоне} (профиль, [ozon_brand]). Если задан, бренд карточек приводится к бренду плана
    операциями set_brand (с id справочника); чужие бренды вне таблицы не трогаются, а попадают в предупреждения."""
    out = DryRun(run_id=plan.run.run_id)
    ids = brand_ids(live) if brand_map else {}
    okcur = allowed_current(brand_map) if brand_map else set()
    fixes: dict[tuple[str, str], str] = {}        # (account, offer) -> новое «Название цвета» из предложений плана
    for t in plan.checks.twins:
        for p in t["proposals"]:
            for offer in p["offers"]:
                fixes[(next(g.account for g in plan.groups if g.group_id == t["group_id"]), offer)] = p["new_value"]
    holders: dict[tuple[str, str], list[str]] = defaultdict(list)       # (кабинет, имя модели) -> артикулы на площадке
    for (acc, off), info in live.items():
        name = _first(info["attrs"], MODEL_NAME_ATTR)
        if name:
            holders[(acc, name)].append(off)
    planned_elsewhere = {(g.account, m.offer_id) for g in plan.groups for d in g.designs for m in d.members}
    for g in plan.groups:
        reasons: list[str] = []
        members = [(g.account, m.offer_id) for d in g.designs for m in d.members]
        out.members += len(members)
        missing = [o for a, o in members if (a, o) not in live]
        if missing:
            reasons.append(f"нет живой карточки: {len(missing)} арт., например {missing[0]}")
        types = {live[k]["cat"] for k in members if k in live}
        if len(types) > 1:
            reasons.append(f"разные категории/типы в группе: {sorted(types)}")
        if g.n_designs > MAX_DESIGNS:
            reasons.append(f"{g.n_designs} дизайнов, больше {MAX_DESIGNS}")
        brand_ops: list[Op] = []
        want = (brand_map or {}).get(g.brand)
        if brand_map is not None and want is None:
            reasons.append(f"бренд плана «{g.brand}» не описан в [ozon_brand]")
        elif want is not None:
            for k in members:
                if k not in live:
                    continue
                battr = brand_attr_of(live[k])
                cur = _first(live[k]["attrs"], battr)
                if cur.casefold() == want.casefold() or (not cur and want.casefold() == NO_BRAND):
                    continue                                    # совпадает; пустой бренд = «Нет бренда» (Озон такую запись пропускает)
                if cur.casefold() not in okcur:
                    out.warnings.append(f"{k[1]}: чужой бренд «{cur}» на Озоне, не меняем")
                    continue
                tid = target_id(ids, k[0], live[k]["cat"], want)
                if tid is None:
                    reasons.append(f"нет id бренда «{want}» среди карточек кабинета {k[0]}")
                    break
                brand_ops.append(Op(k[0], k[1], "set_brand", battr, cur, want, g.group_id, live[k].get("product_id"),
                                    live[k].get("brand_id"), tid))
        changed = {o.offer_id for o in brand_ops}
        brands = {(want if k[1] in changed else _first(live[k]["attrs"], brand_attr_of(live[k]))) for k in members if k in live} - {""}
        if len(brands) > 1:
            out.warnings.append(f"{g.group_id}: в живых карточках несколько брендов {sorted(brands)}")
        # близнецы по живым атрибутам с учётом запланированных правок цвета
        sig_seen: dict[tuple, str] = {}
        for k in members:
            if k not in live:
                continue
            info = {**live[k], "attrs": dict(live[k]["attrs"])}
            if k in fixes:
                info["attrs"][str(COLOR_NAME_ATTR)] = [fixes[k]]
            sig = signature(info, aspects)
            if sig and sig in sig_seen and sig_seen[sig] != k[1]:
                reasons.append(f"близнецы после правок: {sig_seen[sig]} и {k[1]}")
                break
            sig_seen.setdefault(sig, k[1])
        target = g.target.get("ozon", {}).get("model_name", g.group_id)
        own = {o for _, o in members}
        # чужие держатели имени: если они в плане переезжают в другую группу, конфликта нет, они получат своё имя;
        # остаются только те, которые плану не принадлежат вовсе
        foreign = [o for o in holders.get((g.account, target), []) if o not in own and (g.account, o) not in planned_elsewhere]
        if stock is not None:                           # без остатка такие артикулы снимаются операцией unglue_model, это не предупреждение
            foreign = [o for o in foreign if stock.get((g.account, o), 0) >= min_total]
        if foreign:
            out.warnings.append(f"{g.group_id}: имя модели «{target}» останется у {len(foreign)} арт. вне плана, например {foreign[0]}")
        if reasons:
            out.blocked.append(Blocked(g.group_id, reasons))
            continue
        out.ops.extend(brand_ops)
        for k in members:
            info = live[k]
            for typ, attr_id, new in (("set_color_name", COLOR_NAME_ATTR, fixes.get(k)),
                                      ("set_model_name", MODEL_NAME_ATTR, target)):
                if new is None:
                    continue
                cur = _first(info["attrs"], attr_id)
                if cur != new:
                    out.ops.append(Op(k[0], k[1], typ, attr_id, cur, new, g.group_id, info.get("product_id")))
                else:
                    out.already_ok += 1
    if stock is not None:
        targets = {(g.account, g.target.get("ozon", {}).get("model_name", g.group_id)) for g in plan.groups}
        blocked_names = {(g.account, g.target.get("ozon", {}).get("model_name", g.group_id)) for g in plan.groups
                         if g.group_id in {b.group_id for b in out.blocked}}
        for (acc, offer), info in sorted(live.items()):
            cur = _first(info["attrs"], MODEL_NAME_ATTR)
            if (acc, offer) in planned_elsewhere or (acc, cur) not in targets or (acc, cur) in blocked_names:
                continue
            if cur == offer or stock.get((acc, offer), 0) >= min_total:
                continue                                  # уже сам по себе, либо вне плана по другой причине (остаток есть)
            out.ops.append(Op(acc, offer, "unglue_model", MODEL_NAME_ATTR, cur, offer, "", info.get("product_id")))
    return out


OP_TYPES = ("set_brand", "set_color_name", "set_model_name", "unglue_model")


def select_ops(ops: list[Op], group_type: dict[str, str], accounts: set[str] = frozenset(),
               types: set[str] = frozenset(), only: set[str] = frozenset()) -> list[Op]:
    """Отбор операций для частичного применения: по кабинетам, типам товара (у unglue_model типа нет, он зависит от кабинета и вида)
    и видам операций. Пустой фильтр не ограничивает."""
    bad = set(only) - set(OP_TYPES)
    if bad:
        raise ValueError(f"неизвестные виды операций {sorted(bad)}; допустимо {list(OP_TYPES)}")
    out = []
    for o in ops:
        if accounts and o.account not in accounts:
            continue
        if only and o.op_type not in only:
            continue
        if types and o.op_type != "unglue_model" and group_type.get(o.group_id) not in types:
            continue
        if types and o.op_type == "unglue_model":
            continue                                     # снятие по типу товара не фильтруется: тип артикулу вне плана неизвестен
        out.append(o)
    return out
