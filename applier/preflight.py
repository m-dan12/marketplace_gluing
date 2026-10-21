"""Предполётная проверка плана перед записью на Озон. Чистые функции, без БД и сети.

evaluate(plan, res, snapshots, today, limits) -> Report: список проверок (ok / предупреждение / блокировка) и метрики.
Блокировки останавливают применение, предупреждения попадают в отчёт. Те же правила использует автоприменение."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from applier.ops import DryRun
from contract.models import Plan

OK, WARN, STOP = "ok", "warn", "stop"


@dataclass
class Item:
    level: str
    name: str
    detail: str = ""


@dataclass
class Report:
    run_id: str
    items: list[Item] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    @property
    def stops(self) -> list[Item]:
        return [i for i in self.items if i.level == STOP]

    @property
    def warns(self) -> list[Item]:
        return [i for i in self.items if i.level == WARN]

    @property
    def go(self) -> bool:
        return not self.stops

    def add(self, level: str, name: str, detail: str = "") -> None:
        self.items.append(Item(level, name, detail))


def evaluate(plan: Plan, res: DryRun, status: str | None, snapshots: dict[str, str], today: date, limits: dict | None = None) -> Report:
    """snapshots: {"ozon_stock_snapshot": "2026-10-20", "rolls_snapshot": ...} — даты последних снимков данных.
    limits: max_age_days (возраст плана), max_unresolved_twins, max_blocked."""
    lim = {"max_blocked": 0, "max_age_days": 0, **(limits or {})}
    r = Report(plan.run.run_id)
    ck = plan.checks
    members = {m.offer_id for g in plan.groups for d in g.designs for m in d.members}
    group_by_id = {g.group_id: g for g in plan.groups}

    # --- статус и свежесть
    r.add(OK if status == "draft" else STOP, "статус плана", f"{status}" + ("" if status == "draft" else " (применять можно только черновик)"))
    as_of = plan.run.inputs.get("as_of") or plan.run.run_id[:10]
    try:
        age = (today - date.fromisoformat(str(as_of)[:10])).days
    except ValueError:
        age = 999
    r.add(OK if age <= lim["max_age_days"] else STOP, "план за сегодня", f"план на {as_of}, возраст {age} дн.")
    for key, label in (("ozon_stock_snapshot", "остатки Озона"), ("rolls_snapshot", "рулоны Селсапа")):
        snap = snapshots.get(key)
        fresh = snap is not None and snap[:10] >= str(today)
        r.add(OK if fresh else STOP, f"свежесть: {label}", f"снимок {snap}, сегодня {today}")

    # --- проверки самого плана
    r.add(OK if not ck.hard_violations else STOP, "жёсткие нарушения плана", f"{len(ck.hard_violations)}" +
          ("; " + "; ".join(ck.hard_violations[:3]) if ck.hard_violations else ""))
    color_ops = [o for o in res.ops if o.op_type == "set_color_name"]
    twin_groups = {t.get("group_id") for t in ck.twins}
    fixed_by = {o.group_id for o in color_ops}
    # правка уже может стоять на Озоне (после вчерашнего применения операции не нужны): группа решена, если у неё есть предложение
    # правки и сама проверка по живым атрибутам её не заблокировала («близнецы после правок»)
    proposed = {t.get("group_id") for t in ck.twins if t.get("proposals")}
    blocked_ids = {b.group_id for b in res.blocked}
    unresolved = [g for g in twin_groups if g not in fixed_by and not (g in proposed and g not in blocked_ids)]
    r.add(OK if not unresolved else (WARN if len(unresolved) <= lim.get("max_unresolved_twins", 0) else STOP),
          "близнецы без правки цвета", f"групп с близнецами {len(twin_groups)}, из них без правки цвета {len(unresolved)}")
    if twin_groups:
        r.add(OK, "близнецы: правки цвета", f"{len(color_ops)} операций set_color_name для {len(twin_groups) - len(unresolved)} групп")
    nb = len(res.blocked)
    r.add(OK if nb == 0 else (WARN if nb <= lim["max_blocked"] else STOP), "заблокированные группы",
          f"{nb}" + ("; " + "; ".join(f"{b.group_id}: {', '.join(b.reasons)[:80]}" for b in res.blocked[:3]) if nb else ""))
    if res.warnings:
        r.add(WARN, "предупреждения проверки", f"{len(res.warnings)}: " + " | ".join(w[:90] for w in res.warnings[:3]))

    # --- согласованность операций
    keys = Counter((o.account, o.offer_id, o.op_type) for o in res.ops)
    dup = [k for k, n in keys.items() if n > 1]
    r.add(OK if not dup else STOP, "дубли операций", f"{len(dup)}")
    empty = [o for o in res.ops if o.op_type != "unglue_model" and not o.to_value.strip()]
    r.add(OK if not empty else STOP, "пустые новые значения", f"{len(empty)}")
    per_group: dict[str, set[str]] = defaultdict(set)
    for o in res.ops:
        if o.op_type == "set_model_name":
            per_group[o.group_id].add(o.to_value)
    split = [g for g, v in per_group.items() if len(v) > 1]
    r.add(OK if not split else STOP, "одна группа — одно имя модели", f"групп с несколькими именами: {len(split)}")
    by_name: dict[str, set[str]] = defaultdict(set)
    for g, v in per_group.items():
        for name in v:
            by_name[name].add(g)
    clash = [n for n, gs in by_name.items() if len(gs) > 1]
    r.add(OK if not clash else STOP, "имя модели не делят разные группы", f"имён у нескольких групп: {len(clash)}")
    wrong_target = [o for o in res.ops if o.op_type == "set_model_name" and o.group_id in group_by_id
                    and group_by_id[o.group_id].target.get("model_name") not in (None, o.to_value)]
    r.add(OK if not wrong_target else STOP, "имя в операции = цель группы в плане", f"расхождений: {len(wrong_target)}")
    unglue_in_plan = [o for o in res.ops if o.op_type == "unglue_model" and o.offer_id in members]
    r.add(OK if not unglue_in_plan else STOP, "снимаем только артикулы вне плана", f"снятий артикулов из плана: {len(unglue_in_plan)}")

    # --- бренд (set_brand): запись только по id справочника
    brand_ops = [o for o in res.ops if o.op_type == "set_brand"]
    no_id = [o for o in brand_ops if not o.to_id]
    r.add(OK if not no_id else STOP, "бренд: у операций есть id справочника", f"set_brand {len(brand_ops)}, без id: {len(no_id)}")
    if brand_ops:
        trans = Counter((o.from_value or "(пусто)", o.to_value) for o in brand_ops)
        r.add(OK, "бренд: замены", "; ".join(f"{a} -> {b}: {n}" for (a, b), n in trans.most_common(8)))

    # --- размеры результата
    sizes = [g.n_designs for g in plan.groups if g.kind in ("glue", "small_glue")]
    small = sum(1 for g in plan.groups if g.kind == "small_glue")
    r.metrics.update(groups=len(plan.groups), small=small, members=len(members), blocked=nb,
                     designs_min=min(sizes) if sizes else 0, designs_max=max(sizes) if sizes else 0)

    # --- объём
    by_type = Counter(o.op_type for o in res.ops)
    by_acc = Counter((o.account, o.op_type) for o in res.ops)
    r.metrics.update(ops=len(res.ops), by_type=dict(by_type), by_account={f"{a}/{t}": n for (a, t), n in sorted(by_acc.items())},
                     already_ok=res.already_ok, twins=len(twin_groups), color_ops=len(color_ops))
    return r


def render(rep: Report) -> str:
    sign = {OK: "[ОК]   ", WARN: "[ВНИМ.]", STOP: "[СТОП] "}
    lines = [f"Предполётный отчёт по плану {rep.run_id}", "=" * 60]
    for it in rep.items:
        lines.append(f"{sign[it.level]} {it.name}: {it.detail}")
    m = rep.metrics
    lines += ["", f"Склеек {m.get('groups')} (малых {m.get('small')}), артикулов в плане {m.get('members')}, "
              f"дизайнов в склейке {m.get('designs_min')}–{m.get('designs_max')}",
              f"Операций {m.get('ops')}: {m.get('by_type')}; уже верно {m.get('already_ok')}"]
    for k, v in (m.get("by_account") or {}).items():
        lines.append(f"   {k}: {v}")
    lines += ["", ("ВЕРДИКТ: можно применять" if rep.go else f"ВЕРДИКТ: применять нельзя, блокировок {len(rep.stops)}")
              + (f", предупреждений {len(rep.warns)}" if rep.warns else "")]
    return "\n".join(lines)
