from datetime import datetime

from ops.health_check import alerts

MORNING = datetime(2026, 10, 21, 9, 30)
NIGHT = datetime(2026, 10, 21, 3, 0)
OK = {"dead": {}, "stale": {}, "expired": {}, "beats": {"attrs": 20, "apply": 30, "notify": 10}, "sync_today": True, "plan_today": True, "apply_stuck": []}


def kinds(s, now=MORNING):
    return {k for k, _ in alerts(s, now)}


def test_all_good_has_no_alerts():
    assert alerts(OK, MORNING) == []


def test_dead_stale_expired_and_stuck_apply():
    s = {**OK, "dead": {"gluing-plan": 2}, "stale": {"gluing-plan": 35.4}, "expired": {"gluing-apply": 1}, "apply_stuck": ["2026-10-21-ozon"]}
    assert kinds(s) == {"dead-gluing-plan", "stale-gluing-plan", "expired-gluing-apply", "apply-stuck-2026-10-21-ozon"}
    assert any("35 мин" in t for _, t in alerts(s, MORNING))


def test_heartbeat_old_or_missing():
    s = {**OK, "beats": {"attrs": 900, "apply": None, "notify": 5}}
    assert kinds(s) == {"beat-attrs", "beat-apply"}


def test_morning_checks_only_after_deadline_hour():
    s = {**OK, "sync_today": False, "plan_today": False}
    assert kinds(s, NIGHT) == set()
    assert kinds(s, MORNING) == {"no-sync", "no-plan"}


def test_orphan_runs_and_resume_are_bounded(monkeypatch):
    from ops import apply_runner, bus_worker, health_check

    class Cur:
        def __init__(self): self.q = ""
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, args=None): self.q, self.args = q, args
        def fetchall(self): return [("2026-10-21-wb",)] if "apply_started" in self.q else []
        def fetchone(self): return (1,)

    class Conn:
        def cursor(self): return Cur()

    assert health_check.orphan_runs(Conn(), task_running=True) == []                 # задание живо: возобновлять нечего
    runs = health_check.orphan_runs(Conn(), task_running=False)
    assert runs == [("2026-10-21-wb", 1)]
    started, published = [], []
    monkeypatch.setattr(apply_runner, "start_wb", lambda *a, **k: started.append((a, k)))
    monkeypatch.setattr(bus_worker, "conf", lambda name: "x")
    bus = type("B", (), {"publish": lambda self, topic, key, payload, producer: published.append(key)})()
    assert health_check.resume_wb(bus, runs) == ["2026-10-21-wb"]
    assert started[0][1] == {"resume": True} and published == ["wb-resume:2026-10-21-wb:2"]
    assert health_check.resume_wb(bus, [("r", health_check.MAX_RESUMES)]) == []     # лимит возобновлений исчерпан
