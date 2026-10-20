"""Сборка плана (контракт) из результата планировщика WB."""
from __future__ import annotations

from datetime import datetime, timezone

from contract import Check, Design, Group, Member, Plan, RunInfo
from gluing.model import Card
from gluing.parsing import parse_article
from gluing.wb.loader import Loaded
from gluing.wb.planner import WbBuild, WbSet, size_key
from planner.ids import new_id
from planner.plan import profile_hash


def group_key(account: str, s: WbSet) -> str:
    return f"{account}|{s.brand}|{s.category}|{s.rule}|{s.size_key}#{s.part}"


def size_key_of(offer_id: str, profile: dict) -> str:
    info = parse_article(offer_id)
    return size_key(Card(article=offer_id, category="Наволочки", key=info["key"]), profile) if info["key"] else ""


def check_wb_groups(groups: list[Group], profile: dict) -> Check:
    lim = profile["limits"]
    hard: list[str] = []
    warn: list[str] = []
    for g in groups:
        sizes = {size_key_of(m.offer_id, profile) for d in g.designs for m in d.members}
        if g.n_skus > lim["max_group"]:
            hard.append(f"{g.group_id}: {g.n_skus} карточек, больше {lim['max_group']}")
        if len(sizes) > 1:
            hard.append(f"{g.group_id}: в группе несколько размеров {sorted(sizes)}")
        if g.kind == "small_glue":
            warn.append(f"{g.group_id}: малая группа из {g.n_skus} карточек")
        elif g.kind == "single_design":
            warn.append(f"{g.group_id}: одиночная карточка")
        if g.target.get("wb", {}).get("grown"):
            warn.append(f"{g.group_id}: {g.n_skus} карточек, больше рабочего {lim.get('work_group', lim['max_group'])} (допуск роста)")
    return Check(hard_violations=hard, warnings=warn)


def build_wb_groups(account: str, build: WbBuild, live: dict[int, int]) -> list[Group]:
    groups: list[Group] = []
    taken: set[str] = set()
    for s in sorted(build.groups, key=lambda s: (s.category, s.brand, s.rule, s.size_key, s.part)):
        key = group_key(account, s)
        gid = f"wb_{s.imt_id}" if s.imt_id else new_id(key)
        n = 2
        while gid in taken:
            gid, n = new_id(f"{key}#{n}"), n + 1
        taken.add(gid)
        by_design: dict[str, list[Member]] = {}
        for c in sorted(s.cards, key=lambda c: (c.design, c.article)):
            by_design.setdefault(c.design, []).append(Member(
                offer_id=c.article, stock=c.stock_total, orders_28d=c.orders30,
                platform_ids={"nm_id": c.nm_id, "imt_id": live.get(c.nm_id)}))
        groups.append(Group(group_id=gid, key=key, account=account, brand=s.brand, type=s.category, branch=s.rule, part=s.part,
                            kind=s.kind, target={"wb": {"imt_id": s.imt_id, "new": s.imt_id is None, "grown": s.grown}},
                            designs=[Design(design=d, members=ms) for d, ms in by_design.items()],
                            reasons=[f"ветка «{s.rule}»", f"размер {s.size_key}", f"{len(s.cards)} карточек"]))
    return groups


def assemble_plan(groups: list[Group], profiles: dict[str, dict], loaded: dict[str, Loaded], *, run_id: str, engine_version: str,
                  inputs: dict, profile_name: str) -> Plan:
    hard: list[str] = []
    warn: list[str] = []
    for acc, prof in profiles.items():
        ck = check_wb_groups([g for g in groups if g.account == acc], prof)
        hard += ck.hard_violations
        warn += ck.warnings
        ld = loaded[acc]
        if ld.brand_mismatch:
            a, by_prefix, on_wb = ld.brand_mismatch[0]
            warn.append(f"{acc}: у {len(ld.brand_mismatch)} активных карточек бренд на WB не совпадает с брендом по префиксу, "
                        f"например {a}: «{on_wb}» вместо «{by_prefix}»")
        if ld.no_live:
            warn.append(f"{acc}: у {len(ld.no_live)} активных карточек нет живого imtID (wb_card устарел), например {ld.no_live[0]}")
        if ld.unknown_prefix:
            warn.append(f"{acc}: {len(ld.unknown_prefix)} карточек с неизвестным префиксом не склеиваются, например {ld.unknown_prefix[0]}")
    return Plan(
        run=RunInfo(run_id=run_id, platform="wb", engine_version=engine_version, profile_name=profile_name,
                    profile_hash=profile_hash(profiles), inputs=inputs, mode="shadow",
                    created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
        groups=groups, checks=Check(hard_violations=hard, warnings=warn))
