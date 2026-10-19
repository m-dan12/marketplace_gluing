"""Цепочка подписчиков шины на памяти, без БД и Озона."""
import sys
import types

import pytest

pytest.importorskip("busclient")
from busclient import MemoryBus, Router, Worker  # noqa: E402

from ops import bus_worker  # noqa: E402


def make_worker(bus, role):
    name, pattern, make = bus_worker.ROLES[role]
    router = Router()
    router.on(pattern)(make(bus))
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
        name, pattern, _ = bus_worker.ROLES[role]
        bus.register(name, [pattern])
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
