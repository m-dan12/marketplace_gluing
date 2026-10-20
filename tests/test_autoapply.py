from applier.autoapply import Config, decide, load_config
from applier.preflight import OK, STOP, WARN, Report

ON = Config(enabled=True, max_ops=100)


def rep(*levels):
    r = Report("run")
    for i, lv in enumerate(levels):
        r.add(lv, f"проверка {i}", "детали")
    return r


def test_apply_when_everything_is_fine():
    d = decide(rep(OK, WARN), 50, ON, killed=False, running=False)
    assert d.action == "apply" and not d.reasons


def test_off_by_default_and_via_profile():
    assert decide(rep(OK), 5, Config(), False, False).action == "off"
    assert load_config({}).enabled is False
    assert load_config({"autoapply": {"enabled": True, "max_ops": 7}}) == Config(True, 7, "logs/AUTOAPPLY_OFF")


def test_blocked_by_preflight_stop_with_reasons():
    d = decide(rep(OK, STOP), 5, ON, False, False)
    assert d.action == "blocked" and "проверка 1" in d.reasons[0]


def test_blocked_by_kill_switch_running_and_limit():
    assert decide(rep(OK), 5, ON, True, False).action == "blocked"
    assert decide(rep(OK), 5, ON, False, True).action == "blocked"
    d = decide(rep(OK), 101, ON, False, False)
    assert d.action == "blocked" and "лимит" in d.reasons[0]


def test_empty_plan_is_skipped_but_blockers_win_over_empty():
    assert decide(rep(OK), 0, ON, False, False).action == "skip_empty"
    assert decide(rep(STOP), 0, ON, False, False).action == "blocked"
