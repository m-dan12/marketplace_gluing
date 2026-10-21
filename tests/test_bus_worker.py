"""Цепочка подписчиков шины на памяти, без БД и Озона."""
import sys
import types

import pytest

pytest.importorskip("busclient")
from busclient import MemoryBus, Router, Worker  # noqa: E402

from ops import bus_worker  # noqa: E402


def make_worker(bus, role):
    name, patterns, make = bus_worker.ROLES[role]
    router = Router()
    handler = make(bus)
    for pattern in patterns:
        router.on(pattern)(handler)
    return Worker(bus, name, router, max_attempts=2, visibility_sec=60)


def setup(monkeypatch, result=None, fail=False):
    calls = []

    def fake_run_plan(as_of, dry=False, **kw):
        calls.append((as_of, dry))
        if fail:
            raise SystemExit("жёсткие нарушения")
        return result or {"run_id": f"{as_of}-ozon", "saved": not dry, "groups": 113, "small": 15, "churn": {}, "twins": 0}

    monkeypatch.setitem(sys.modules, "plan_ozon", types.SimpleNamespace(run_plan=fake_run_plan))
    bus = MemoryBus()
    for role in bus_worker.ROLES:
        name, patterns, _ = bus_worker.ROLES[role]
        bus.register(name, patterns)
    return bus, calls


def test_plan_stage_publishes_plan_ready(monkeypatch):
    bus, calls = setup(monkeypatch)
    bus.publish("gluing.ozon.attrs_ready", "2026-10-19", {"as_of": "2026-10-19"})
    assert make_worker(bus, "plan").run_once() == 1
    assert calls == [("2026-10-19", False)]
    ev = bus.claim("gluing-apply", "w")[0]
    assert ev.topic == "gluing.ozon.plan_ready" and ev.payload["run_id"] == "2026-10-19-ozon" and ev.key == "2026-10-19"


def test_plan_not_repeated_on_redelivery(monkeypatch):
    """Упали после публикации plan_ready, но до ack: событие придёт снова, второй план строиться не должен."""
    bus, calls = setup(monkeypatch)
    bus.publish("gluing.ozon.attrs_ready", "2026-10-19")
    ev = bus.claim("gluing-plan", "w")[0]
    h = bus_worker.handler_plan(bus)
    h(ev)
    h(ev)
    assert calls == [("2026-10-19", False)]
    assert len(bus.claim("gluing-apply", "w", 10)) == 1


def test_dry_event_does_not_save_and_skips_apply(monkeypatch):
    bus, calls = setup(monkeypatch)
    bus.publish("gluing.ozon.attrs_ready", "t1", {"dry": True, "as_of": "2026-10-19"})
    make_worker(bus, "plan").run_once()
    assert calls == [("2026-10-19", True)]
    make_worker(bus, "apply").run_once()
    assert bus.status("gluing-apply") == {"done": 1}                              # обработано без dry-run и без публикации
    assert bus.claim("gluing-plan", "w") == []


def test_systemexit_in_plan_becomes_retry_not_crash(monkeypatch):
    bus, _ = setup(monkeypatch, fail=True)
    bus.publish("gluing.ozon.attrs_ready", "2026-10-19")
    w = make_worker(bus, "plan")
    assert w.run_once() == 1                                                      # процесс жив
    assert bus.status("gluing-plan") == {"pending": 1}


def test_as_of_from_key_or_today():
    ev = types.SimpleNamespace(payload={}, key="2026-10-19")
    assert bus_worker.as_of_of(ev) == "2026-10-19"
    ev = types.SimpleNamespace(payload={}, key="manual-run")
    assert len(bus_worker.as_of_of(ev)) == 10


# ---------- WB: три стадии одного подписчика ----------

def wb_setup(monkeypatch):
    bus = MemoryBus()
    for role in bus_worker.ROLES:
        name, patterns, _ = bus_worker.ROLES[role]
        bus.register(name, patterns)
    return bus


def test_wb_chain_live_then_plan_then_apply_blocked_without_tokens(monkeypatch):
    bus = wb_setup(monkeypatch)
    calls = []
    monkeypatch.setitem(sys.modules, "plan_wb", types.SimpleNamespace(
        run_plan=lambda as_of, dry=False, **kw: calls.append((as_of, dry)) or {"run_id": f"{as_of}-wb", "saved": not dry, "groups": 400,
                                                                                  "hard_violations": []}))
    bus.publish("sync.wb.done", "2026-10-21", {"as_of": "2026-10-21", "dry": True})
    w = make_worker(bus, "wb")
    assert w.run_once() == 1                                   # dry: склейки не читаем, событие идёт дальше
    assert w.run_once() == 1                                   # gluing.wb.live_ready -> план
    assert calls == [("2026-10-21", True)]
    assert w.run_once() == 1                                   # gluing.wb.plan_ready (dry) -> проверка пропущена
    assert bus.status("gluing-wb") == {"done": 3}


def test_wb_stages_are_idempotent_on_redelivery(monkeypatch):
    bus = wb_setup(monkeypatch)
    calls = []
    monkeypatch.setitem(sys.modules, "plan_wb", types.SimpleNamespace(
        run_plan=lambda as_of, dry=False, **kw: calls.append(as_of) or {"run_id": f"{as_of}-wb", "groups": 1}))
    bus.publish("gluing.wb.live_ready", "2026-10-21", {"as_of": "2026-10-21"})
    ev = bus.claim("gluing-wb", "w")[0]
    h = bus_worker.handler_wb(bus)
    h(ev)
    h(ev)
    assert calls == ["2026-10-21"]


def test_wb_apply_stage_blocks_and_publishes_reasons(monkeypatch):
    """Нет токена склеек: запись не стартует, в шину уходит apply_blocked с причиной."""
    import psycopg
    from applier.wb_ops import WbDryRun
    from contract import Design, Group, Member, Plan, RunInfo
    bus = wb_setup(monkeypatch)
    plan = Plan(run=RunInfo(run_id="2026-10-21-wb", platform="wb", created_at="x", engine_version="t", profile_name="p",
                            profile_hash="h", inputs={"as_of": "2026-10-21"}),
                groups=[Group(group_id="g", key="k", account="skazka", brand="B", type="T", branch="b", kind="glue",
                              designs=[Design(design="1", members=[Member(offer_id="A1", platform_ids={"nm_id": 1})])])])

    class Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class Store:
        def __init__(self, conn): pass
        def load(self, rid): return plan
        def status(self, rid): return "draft"
        def set_status(self, *a): raise AssertionError("запись не должна стартовать")

    from applier.ops import Op
    res = WbDryRun(run_id="2026-10-21-wb")
    res.ops = [Op("skazka", "A1", "move_nm", 0, "5", "10", "g", 1)]
    from datetime import date
    today = date.today().isoformat()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: Conn())
    monkeypatch.setitem(sys.modules, "plan_ozon", types.SimpleNamespace(database_url=lambda: "x"))
    monkeypatch.setitem(sys.modules, "apply_wb", types.SimpleNamespace(
        check_plan_wb=lambda conn, plan: res,
        snapshots_now=lambda conn, plan: {"wb_stock_snapshot": today, "rolls_snapshot": today,
                                          "wb_live_at": __import__("datetime").datetime.now().astimezone().isoformat()}))
    monkeypatch.setitem(sys.modules, "planner.store", types.SimpleNamespace(PgStore=Store))
    monkeypatch.setattr(bus_worker, "conf", lambda name: "")
    monkeypatch.setitem(sys.modules, "applier.wb_client", types.SimpleNamespace(
        merge_accounts=lambda env: set(), read_env_files=lambda a, b: ({}, {})))
    from ops import apply_runner
    monkeypatch.setattr(apply_runner, "is_running", lambda task=None: False)
    bus.publish("gluing.wb.plan_ready", "2026-10-21", {"run_id": "2026-10-21-wb", "as_of": today})
    h = bus_worker.handler_wb(bus)
    h(bus.claim("gluing-wb", "w")[0])
    ev = bus.claim("gluing-notify", "n", 10)
    assert [e.topic for e in ev] == ["gluing.wb.apply_blocked"]
    assert any("токен склеек" in r for r in ev[0].payload["reasons"])
