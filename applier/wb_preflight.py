"""Предполётная проверка плана перед записью на WB. Чистые функции, без БД и сети.

evaluate_wb(plan, res, status, snapshots, today, now, limits) -> Report (тот же формат, что у Озона). Блокировки останавливают
применение, предупреждения попадают в отчёт."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone

from applier.preflight import OK, STOP, WARN, Report
from applier.wb_ops import HARD_MAX, NEW, WbDryRun, nm_of
from contract.models import Plan


def evaluate_wb(plan: Plan, res: WbDryRun, status: str | None, snapshots: dict[str, str], today: date,
                now: datetime | None = None, limits: dict | None = None) -> Report:
    """snapshots: wb_stock_snapshot, rolls_snapshot (даты), wb_live_at (когда перечитаны склейки с WB).
    limits: max_blocked, max_age_days, max_live_age_min, max_ops."""
    lim = {"max_blocked": 0, "max_age_days": 0, "max_live_age_min": 180, "max_ops": 1500, **(limits or {})}
    now = now or datetime.now(timezone.utc)
    r = Report(plan.run.run_id)
    ck = plan.checks
    members = [(g.account, nm_of(m), m.offer_id, g.group_id) for g in plan.groups for d in g.designs for m in d.members]
    in_plan = {(a, o) for a, _, o, _ in members}
    group_by_id = {g.group_id: g for g in plan.groups}

    r.add(OK if status == "draft" else STOP, "статус плана", f"{status}" + ("" if status == "draft" else " (применять можно только черновик)"))
    r.add(OK if plan.run.platform == "wb" else STOP, "площадка плана", plan.run.platform)
    as_of = plan.run.inputs.get("as_of") or plan.run.run_id[:10]
    try:
        age = (today - date.fromisoformat(str(as_of)[:10])).days
    except ValueError:
        age = 999
    r.add(OK if age <= lim["max_age_days"] else STOP, "план за сегодня", f"план на {as_of}, возраст {age} дн.")
    for key, label in (("wb_stock_snapshot", "остатки WB"), ("rolls_snapshot", "рулоны Селсапа")):
        snap = snapshots.get(key)
        r.add(OK if snap is not None and snap[:10] >= str(today) else STOP, f"свежесть: {label}", f"снимок {snap}, сегодня {today}")
    live_at = snapshots.get("wb_live_at") or ""
    try:
        t = datetime.fromisoformat(live_at)
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        mins = int((now - t).total_seconds() // 60)
    except ValueError:
        mins = 10**6
    r.add(OK if mins <= lim["max_live_age_min"] else STOP, "свежесть: склейки WB (imtID)",
          f"перечитаны {live_at or 'никогда'}, {mins if mins < 10**6 else '—'} мин назад, допустимо {lim['max_live_age_min']}")

    r.add(OK if not ck.hard_violations else STOP, "жёсткие нарушения плана",
          f"{len(ck.hard_violations)}" + ("; " + "; ".join(ck.hard_violations[:3]) if ck.hard_violations else ""))
    nb = len(res.blocked)
    r.add(OK if nb == 0 else (WARN if nb <= lim["max_blocked"] else STOP), "заблокированные группы",
          f"{nb}" + ("; " + "; ".join(f"{b.group_id}: {', '.join(b.reasons)[:80]}" for b in res.blocked[:3]) if nb else ""))
    if ck.warnings:
        r.add(WARN, "предупреждения плана", f"{len(ck.warnings)}: " + " | ".join(w[:90] for w in ck.warnings[:3]))
    if res.warnings:
        r.add(WARN, "предупреждения проверки", f"{len(res.warnings)}: " + " | ".join(w[:90] for w in res.warnings[:3]))

    # --- согласованность плана
    over = [g.group_id for g in plan.groups if g.n_skus > HARD_MAX]
    r.add(OK if not over else STOP, f"склейка не больше {HARD_MAX} карточек", f"превышений: {len(over)}")
    seen: dict[tuple[str, int], str] = {}
    twice = []
    for a, nm, o, gid in members:
        if nm is not None and (a, nm) in seen and seen[(a, nm)] != gid:
            twice.append(o)
        seen[(a, nm)] = gid
    r.add(OK if not twice else STOP, "карточка в одной группе", f"в двух группах: {len(twice)}")
    claimed: dict[tuple[str, int], list[str]] = defaultdict(list)
    for g in plan.groups:
        imt = g.target.get("wb", {}).get("imt_id")
        if imt is not None:
            claimed[(g.account, int(imt))].append(g.group_id)
    dup_imt = [k for k, v in claimed.items() if len(v) > 1]
    r.add(OK if not dup_imt else STOP, "склейку не делят разные группы", f"склеек у нескольких групп: {len(dup_imt)}")

    # --- согласованность операций
    keys = Counter((o.account, o.offer_id) for o in res.ops)
    dup = [k for k, n in keys.items() if n > 1]
    r.add(OK if not dup else STOP, "дубли операций", f"{len(dup)}")
    stray = [o for o in res.ops if (o.account, o.offer_id) not in in_plan]
    r.add(OK if not stray else STOP, "трогаем только карточки из плана", f"операций над чужими карточками: {len(stray)}")
    per_group: dict[str, set[str]] = defaultdict(set)
    for o in res.ops:
        per_group[o.group_id].add(o.to_value)
    split = [g for g, v in per_group.items() if len(v) > 1]
    r.add(OK if not split else STOP, "одна группа — одна целевая склейка", f"групп с несколькими целями: {len(split)}")
    by_target: dict[tuple[str, str], set[str]] = defaultdict(set)
    for o in res.ops:
        by_target[(o.account, o.to_value)].add(o.group_id)
    clash = [k for k, gs in by_target.items() if len(gs) > 1]
    r.add(OK if not clash else STOP, "целевую склейку не делят разные группы", f"склеек у нескольких групп: {len(clash)}")
    wrong = [o for o in res.ops if not o.to_value.startswith(NEW) and res.targets.get(o.group_id) is not None
             and int(o.to_value) != res.targets[o.group_id]]
    r.add(OK if not wrong else STOP, "цель операции = выбранная склейка группы", f"расхождений: {len(wrong)}")
    r.add(OK if len(res.ops) <= lim["max_ops"] else STOP, "объём операций", f"{len(res.ops)}, допустимо {lim['max_ops']}")

    sizes = [g.n_skus for g in plan.groups if g.kind == "glue"]
    small = sum(1 for g in plan.groups if g.kind == "small_glue")
    by_acc = Counter(o.account for o in res.ops)
    r.metrics.update(groups=len(plan.groups), small=small, singles=sum(1 for g in plan.groups if g.kind == "single_design"),
                     members=len(members), blocked=nb, size_min=min(sizes) if sizes else 0, size_max=max(sizes) if sizes else 0,
                     ops=len(res.ops), by_account=dict(by_acc), already_ok=res.already_ok,
                     new_groups=sum(1 for t in res.targets.values() if t is None),
                     grown=sum(1 for g in plan.groups if g.target.get("wb", {}).get("grown")))
    return r


def render_wb(rep: Report) -> str:
    sign = {OK: "[ОК]   ", WARN: "[ВНИМ.]", STOP: "[СТОП] "}
    lines = [f"Предполётный отчёт по плану WB {rep.run_id}", "=" * 60]
    for it in rep.items:
        lines.append(f"{sign[it.level]} {it.name}: {it.detail}")
    m = rep.metrics
    lines += ["", f"Склеек {m.get('groups')} (малых {m.get('small')}, одиночек {m.get('singles')}, выросших сверх рабочего лимита {m.get('grown')}), "
              f"карточек в плане {m.get('members')}, размер склейки {m.get('size_min')}–{m.get('size_max')}",
              f"Операций переноса {m.get('ops')} (новых склеек {m.get('new_groups')}); уже на месте {m.get('already_ok')}"]
    for k, v in (m.get("by_account") or {}).items():
        lines.append(f"   {k}: {v}")
    lines += ["", ("ВЕРДИКТ: можно применять" if rep.go else f"ВЕРДИКТ: применять нельзя, блокировок {len(rep.stops)}")
              + (f", предупреждений {len(rep.warns)}" if rep.warns else "")]
    return "\n".join(lines)
