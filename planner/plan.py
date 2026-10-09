"""Сборка плана (контракт) из результата дерева разбиений Озона."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from contract import Check, Decision, Design, Group, Member, Plan, RunInfo
from gluing.ozon.model import OGroup
from planner.ids import assign_ids

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


def build_plan(groups: list[OGroup], log: list[dict], state: dict, profile: dict, *, run_id: str,
               profile_name: str = "ozon", engine_version: str = "dev", inputs: dict | None = None,
               platform_ids: dict[tuple[str, str], dict] | None = None,
               prev_ids: dict[str, str] | None = None, live_models: dict[str, str] | None = None,
               mode: str = "shadow", created_at: str | None = None) -> Plan:
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
        groups=out, unglued=unglued, checks=check_groups(out, profile),
        decisions=[Decision(**x) for x in log], state=state)
