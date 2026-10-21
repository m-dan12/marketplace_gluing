from datetime import date, datetime, timezone

from applier.wb_ops import dry_run_wb
from applier.wb_preflight import evaluate_wb, render_wb
from test_wb_applier import group, plan

TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc)
SNAPS = {"wb_stock_snapshot": "2026-10-21", "rolls_snapshot": "2026-10-21", "wb_live_at": "2026-10-21T09:00:00+00:00"}


def pl(*groups, as_of="2026-10-21"):
    p = plan(*groups)
    p.run.inputs["as_of"] = as_of
    return p


def lv(rep):
    return {i.name: i.level for i in rep.items}


def ev(p, live, status="draft", snaps=SNAPS, limits=None):
    return evaluate_wb(p, dry_run_wb(p, live), status, snaps, TODAY, NOW, limits)


LIVE = {("skazka", 1): 10, ("skazka", 2): 10, ("skazka", 3): 20}


def test_clean_plan_goes():
    rep = ev(pl(group("g", [1, 2, 3], 10)), LIVE)
    assert rep.go and "ВЕРДИКТ: можно применять" in render_wb(rep)


def test_stale_plan_and_snapshots_stop():
    rep = ev(pl(group("g", [1, 2, 3], 10), as_of="2026-10-19"), LIVE, snaps={**SNAPS, "wb_stock_snapshot": "2026-10-20"})
    assert not rep.go and lv(rep)["план за сегодня"] == "stop" and lv(rep)["свежесть: остатки WB"] == "stop"


def test_old_live_state_stops():
    rep = ev(pl(group("g", [1, 2, 3], 10)), LIVE, snaps={**SNAPS, "wb_live_at": "2026-10-21T01:00:00+00:00"})
    assert lv(rep)["свежесть: склейки WB (imtID)"] == "stop"
    rep = ev(pl(group("g", [1, 2, 3], 10)), LIVE, snaps={k: v for k, v in SNAPS.items() if k != "wb_live_at"})
    assert not rep.go


def test_non_draft_stops():
    assert not ev(pl(group("g", [1, 2, 3], 10)), LIVE, status="applied").go


def test_blocked_group_stops_by_default_warns_with_limit():
    p = pl(group("g", [1, 2, 9], 10))
    assert lv(ev(p, LIVE))["заблокированные группы"] == "stop"
    assert lv(ev(p, LIVE, limits={"max_blocked": 1}))["заблокированные группы"] == "warn"


def test_card_in_two_groups_is_rejected_by_contract_and_imt_claimed_twice_stops():
    p = pl(group("a", [1, 2], 10), group("b", [3], 10))
    assert lv(ev(p, LIVE))["склейку не делят разные группы"] == "stop"


def test_hard_violations_and_ops_limit_stop():
    p = pl(group("g", [1, 2, 3], 10))
    p.checks.hard_violations.append("g: несколько размеров")
    assert lv(ev(p, LIVE))["жёсткие нарушения плана"] == "stop"
    assert lv(ev(pl(group("g", [1, 2, 3], 10)), LIVE, limits={"max_ops": 0}))["объём операций"] == "stop"


def test_missing_merge_token_blocks_only_when_writing():
    p = pl(group("g", [1, 2, 3], 10))
    assert lv(evaluate_wb(p, dry_run_wb(p, LIVE), "draft", SNAPS, TODAY, NOW))["свежесть: остатки WB"] == "ok"
    no_token = evaluate_wb(p, dry_run_wb(p, LIVE), "draft", SNAPS, TODAY, NOW, merge_accounts=set())
    assert not no_token.go and lv(no_token)["токен склеек WB"] == "stop"
    with_token = evaluate_wb(p, dry_run_wb(p, LIVE), "draft", SNAPS, TODAY, NOW, merge_accounts={"skazka"})
    assert with_token.go and lv(with_token)["токен склеек WB"] == "ok"
