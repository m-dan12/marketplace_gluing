from datetime import date

from applier.ops import Blocked, DryRun, Op
from applier.preflight import STOP, WARN, evaluate, render
from contract.models import Check, Design, Group, Member, Plan, RunInfo

TODAY = date(2026, 10, 20)
SNAPS = {"ozon_stock_snapshot": "2026-10-20", "rolls_snapshot": "2026-10-20"}


def plan(twins=None, hard=None, as_of="2026-10-20"):
    g = Group(group_id="g1", key="k", account="skazka", brand="B", type="Штора", branch="Обычные", kind="glue",
              target={"model_name": "M1"},
              designs=[Design(design="d1", members=[Member(offer_id="A1")]), Design(design="d2", members=[Member(offer_id="A2")])])
    run = RunInfo(run_id="2026-10-20-ozon", platform="ozon", created_at="x", engine_version="t", profile_name="p",
                  profile_hash="h", inputs={"as_of": as_of, **SNAPS})
    return Plan(run=run, groups=[g], checks=Check(hard_violations=hard or [], twins=twins or []))


def op(offer="A1", typ="set_model_name", to="M1", gid="g1", frm="old"):
    return Op(account="skazka", offer_id=offer, op_type=typ, attr_id=9048, from_value=frm, to_value=to, group_id=gid)


def run_of(*ops, blocked=(), warnings=()):
    return DryRun(run_id="2026-10-20-ozon", ops=list(ops), blocked=list(blocked), warnings=list(warnings), members=2)


def levels(rep):
    return {i.name: i.level for i in rep.items}


def test_clean_plan_goes():
    rep = evaluate(plan(), run_of(op("A1"), op("A2")), "draft", SNAPS, TODAY)
    assert rep.go and not rep.stops
    assert "ВЕРДИКТ: можно применять" in render(rep)


def test_stale_snapshot_and_old_plan_stop():
    rep = evaluate(plan(as_of="2026-10-18"), run_of(op()), "draft", {**SNAPS, "ozon_stock_snapshot": "2026-10-19"}, TODAY)
    assert not rep.go
    lv = levels(rep)
    assert lv["план за сегодня"] == STOP and lv["свежесть: остатки Озона"] == STOP


def test_hard_violation_stops():
    rep = evaluate(plan(hard=["группа 19 дизайнов"]), run_of(op()), "draft", SNAPS, TODAY)
    assert levels(rep)["жёсткие нарушения плана"] == STOP


def test_non_draft_status_stops():
    assert not evaluate(plan(), run_of(op()), "applied", SNAPS, TODAY).go


def test_twins_fixed_by_color_ops_is_ok_and_unfixed_stops():
    tw = [{"group_id": "g1", "group_key": "k", "designs": ["d1", "d2"], "color": "Бисер", "proposals": []}]
    fixed = evaluate(plan(twins=tw), run_of(op(), op("A2", "set_color_name", "Бисер 2")), "draft", SNAPS, TODAY)
    assert levels(fixed)["близнецы без правки цвета"] == "ok"
    unfixed = evaluate(plan(twins=tw), run_of(op()), "draft", SNAPS, TODAY)
    assert levels(unfixed)["близнецы без правки цвета"] == STOP


def test_blocked_groups_stop_by_default_and_warn_with_limit():
    b = Blocked("g9", ["имя занято"])
    assert levels(evaluate(plan(), run_of(op(), blocked=[b]), "draft", SNAPS, TODAY))["заблокированные группы"] == STOP
    assert levels(evaluate(plan(), run_of(op(), blocked=[b]), "draft", SNAPS, TODAY, {"max_blocked": 1}))["заблокированные группы"] == WARN


def test_name_collision_between_groups_and_split_names_stop():
    rep = evaluate(plan(), run_of(op("A1", to="M1", gid="g1"), op("A2", to="M1", gid="g2")), "draft", SNAPS, TODAY)
    assert levels(rep)["имя модели не делят разные группы"] == STOP
    rep = evaluate(plan(), run_of(op("A1", to="M1"), op("A2", to="M2")), "draft", SNAPS, TODAY)
    assert levels(rep)["одна группа — одно имя модели"] == STOP


def test_duplicates_empty_values_and_unglue_of_plan_member_stop():
    assert levels(evaluate(plan(), run_of(op(), op()), "draft", SNAPS, TODAY))["дубли операций"] == STOP
    assert levels(evaluate(plan(), run_of(op(to=" ")), "draft", SNAPS, TODAY))["пустые новые значения"] == STOP
    rep = evaluate(plan(), run_of(op("A1", "unglue_model", "A1")), "draft", SNAPS, TODAY)
    assert levels(rep)["снимаем только артикулы вне плана"] == STOP
    rep = evaluate(plan(), run_of(op("Z9", "unglue_model", "Z9", gid="")), "draft", SNAPS, TODAY)
    assert levels(rep)["снимаем только артикулы вне плана"] == "ok"


def test_target_mismatch_stops():
    rep = evaluate(plan(), run_of(op(to="OTHER")), "draft", SNAPS, TODAY)
    assert levels(rep)["имя в операции = цель группы в плане"] == STOP
