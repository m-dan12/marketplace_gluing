"""Применяющий скрипт Озона, часть 1: разница «план → живое состояние» и предварительные проверки (dry-run).

Ничего не пишет на площадку. Вход: план (contract.Plan) и живое состояние карточек (атрибуты по артикулам).
Выход: список операций, заблокированные группы и причины. Идемпотентность: артикул, у которого значение уже верное, пропускается.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

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
    op_type: str                # set_model_name | set_color_name
    attr_id: int
    from_value: str
    to_value: str
    group_id: str
    product_id: int | None = None


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


def dry_run(plan: Plan, live: dict[tuple[str, str], dict], aspects: dict) -> DryRun:
    """live: {(account, offer_id): {"cat": (category_id, type_id), "attrs": {attr_id: [values]}, "product_id": int|None}}"""
    out = DryRun(run_id=plan.run.run_id)
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
        brands = {_first(live[k]["attrs"], BRAND_ATTR) for k in members if k in live} - {""}
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
        foreign = [o for o in holders.get((g.account, target), []) if o not in own]
        if foreign:                                   # имя модели уже занято чужими артикулами: после записи они склеятся с группой
            reasons.append(f"имя модели «{target}» уже у {len(foreign)} чужих арт., например {foreign[0]}")
        if reasons:
            out.blocked.append(Blocked(g.group_id, reasons))
            continue
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
    return out
