"""Применяющий скрипт WB, часть 1: разница «план → живое состояние склеек» и предварительные проверки (dry-run).

Ничего не пишет на площадку. Вход: план (contract.Plan) и живое состояние: в какой склейке (imtID) лежит каждая карточка.
Выход: операции move_nm, заблокированные группы и предупреждения.

Идемпотентность: операции строятся только из живого состояния. Карточка, уже лежащая в целевой склейке, пропускается, поэтому
повторный запуск после обрыва делает только остаток. Целевая склейка группы — imtID из плана, если он ещё существует; иначе
«чистая» склейка (в ней только карточки этой группы) с наибольшим числом её карточек; если такой нет, группа новая:
одна карточка отделяется, остальные присоединяются к ней (to_value = «new:<group_id>»).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from applier.ops import Blocked, DryRun, Op
from contract import Plan

HARD_MAX = 30                # жёсткий лимит WB на склейку
NEW = "new:"                 # to_value операции в ещё не созданную склейку группы


@dataclass
class WbDryRun(DryRun):
    sizes: dict[str, dict[int, int]] = field(default_factory=dict)       # {кабинет: {imtID: сколько карточек сейчас}}
    targets: dict[str, int | None] = field(default_factory=dict)         # {group_id: выбранная склейка или None (новая)}

    def summary(self) -> dict:
        s = super().summary()
        s["групп новых"] = sum(1 for t in self.targets.values() if t is None)
        return s


def nm_of(member) -> int | None:
    v = member.platform_ids.get("nm_id")
    return int(v) if v is not None else None


def dry_run_wb(plan: Plan, live: dict[tuple[str, int], int], max_size: int = HARD_MAX) -> WbDryRun:
    """live: {(кабинет, nm_id): текущий imtID} по всем карточкам кабинета, не только активным."""
    out = WbDryRun(run_id=plan.run.run_id)
    residents: dict[tuple[str, int], set[int]] = {}
    for (acc, nm), imt in live.items():
        residents.setdefault((acc, imt), set()).add(nm)
    out.sizes = {}
    for (acc, imt), nms in residents.items():
        out.sizes.setdefault(acc, {})[imt] = len(nms)
    planned = {(g.account, nm_of(m)) for g in plan.groups for d in g.designs for m in d.members if nm_of(m) is not None}
    group_of = {(g.account, nm_of(m)): g.group_id for g in plan.groups for d in g.designs for m in d.members}
    for g in plan.groups:
        members = [(m.offer_id, nm_of(m)) for d in g.designs for m in d.members]
        out.members += len(members)
        reasons: list[str] = []
        if any(nm is None for _, nm in members):
            reasons.append("у части карточек нет nm_id")
        missing = [o for o, nm in members if nm is not None and (g.account, nm) not in live]
        if missing:
            reasons.append(f"нет живой карточки на WB: {len(missing)} шт., например {missing[0]}")
        if reasons:
            out.blocked.append(Blocked(g.group_id, reasons))
            continue
        own = {nm for _, nm in members}
        want = g.target.get("wb", {}).get("imt_id")
        if want is not None and not residents.get((g.account, int(want))):
            want = None                                                   # склейки больше нет: выбираем заново
        if want is None:
            cnt = Counter(live[(g.account, nm)] for nm in own)
            pure = [(c, imt) for imt, c in cnt.items() if residents[(g.account, imt)] <= own]
            if pure:                                                      # чистая склейка с максимумом карточек группы; при равенстве меньший imtID
                best = max(c for c, _ in pure)
                want = min(imt for c, imt in pure if c == best)
        out.targets[g.group_id] = int(want) if want is not None else None
        if want is not None:                                              # место: остаются только карточки, которые не уходят по плану
            stay = {nm for nm in residents[(g.account, int(want))] - own if (g.account, nm) not in planned}
            if len(own) + len(stay) > max_size:
                out.blocked.append(Blocked(g.group_id, [f"в склейке {want} не хватит места: {len(own)} карточек плана и "
                                                        f"{len(stay)} карточек вне плана, лимит {max_size}"]))
                out.targets.pop(g.group_id)
                continue
            for o, nm in members:
                cur = live[(g.account, nm)]
                if cur != want:
                    out.ops.append(Op(g.account, o, "move_nm", 0, str(cur), str(want), g.group_id, nm))
                else:
                    out.already_ok += 1
        else:
            for i, (o, nm) in enumerate(sorted(members, key=lambda x: x[0])):
                out.ops.append(Op(g.account, o, "move_nm", 0, str(live[(g.account, nm)]), NEW + g.group_id, g.group_id, nm))
    return out
