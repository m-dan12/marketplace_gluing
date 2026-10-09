import tomllib
from pathlib import Path

import pytest

from contract import Plan
from gluing.ozon.loader import load_designs
from gluing.ozon.tree import build
from planner.ids import assign_ids, new_id
from planner.plan import build_plan
from planner.store import KEEP, MemoryStore

ROOT = Path(__file__).resolve().parent.parent
PROFILE = tomllib.load(open(ROOT / "profiles" / "ozon.toml", "rb"))
FIX = ROOT / "fixtures" / "g_ozon.csv"


# ---------- id групп ----------
def test_new_id_is_stable_and_short():
    assert new_id("a|b|c|d#1") == new_id("a|b|c|d#1") != new_id("a|b|c|d#2")
    assert new_id("x").startswith("g_") and len(new_id("x")) == 10


def test_prev_id_wins_by_key():
    ids = assign_ids([("k1", ["a", "b"])], prev_ids={"k1": "g_old"}, live_models={"a": "Model", "b": "Model"})
    assert ids["k1"] == "g_old"


def test_inherit_live_model_name_when_half_match():
    ids = assign_ids([("k1", ["a", "b", "c", "d"])], live_models={"a": "M1", "b": "M1", "c": "M2"})
    assert ids["k1"] == "M1"                                  # 2 из 4 = 50%


def test_no_inherit_below_half_and_name_not_reused():
    ids = assign_ids([("k1", ["a", "b", "c", "d"]), ("k2", ["e", "f"])],
                     live_models={"a": "M1", "e": "M1", "f": "M1"})
    assert ids["k1"] == new_id("k1")                         # 1 из 4 — мало
    assert ids["k2"] == "M1"
    assert len(set(ids.values())) == 2


def test_inherit_goes_to_bigger_group_first():
    ids = assign_ids([("small", ["a", "b"]), ("big", ["a", "b", "c", "d"])], live_models={"a": "M", "b": "M", "c": "M"})
    assert ids["big"] == "M" and ids["small"] == new_id("small")


# ---------- план и контракт ----------
@pytest.fixture(scope="module")
def built():
    if not FIX.exists():
        pytest.skip("нет фикстуры")
    designs, _ = load_designs(FIX, PROFILE)
    groups, log, state = build(designs, PROFILE)
    return groups, log, state


def make(built, run_id="2026-10-09-ozon", **kw):
    groups, log, state = built
    return build_plan(groups, log, state, PROFILE, run_id=run_id, created_at="2026-10-09T06:00:00Z", **kw)


def test_plan_covers_every_sku_once(built):
    groups, _, _ = built
    plan = make(built)
    in_plan = {m.offer_id for g in plan.groups for d in g.designs for m in d.members}
    in_unglued = {u["offer_id"] for u in plan.unglued}
    total = {s.offer_id for g in groups for d in g.designs for s in d.skus}
    assert in_plan | in_unglued == total and not (in_plan & in_unglued)


def test_plan_has_no_hard_violations(built):
    assert make(built).checks.hard_violations == []


def test_plan_group_target_is_group_id(built):
    for g in make(built).groups:
        assert g.target == {"ozon": {"model_name": g.group_id}}


def test_plan_json_roundtrip(built):
    plan = make(built)
    assert Plan.loads(plan.dumps()) == plan


def test_plan_is_deterministic(built):
    assert make(built).dumps() == make(built).dumps()


def test_plan_ids_follow_prev_run(built):
    p1 = make(built)
    prev = {g.key: g.group_id for g in p1.groups}
    p2 = make(built, run_id="2026-10-10-ozon", prev_ids=prev)
    assert {g.key: g.group_id for g in p2.groups} == prev


def test_plan_rejects_duplicate_offer(built):
    plan = make(built)
    bad = plan.model_dump()
    bad["groups"][1]["designs"][0]["members"].append(bad["groups"][0]["designs"][0]["members"][0])
    with pytest.raises(ValueError):
        Plan.model_validate(bad)


def test_plan_rejects_unknown_schema_version(built):
    bad = make(built).model_dump()
    bad["schema_version"] = 2
    with pytest.raises(ValueError):
        Plan.model_validate(bad)


# ---------- хранилище ----------
def test_memory_store_one_draft_per_platform(built):
    st = MemoryStore()
    st.save_draft(make(built, run_id="r1"))
    st.save_draft(make(built, run_id="r2"))
    assert st.status("r1") == "expired" and st.status("r2") == "draft"
    with pytest.raises(ValueError):
        st.save_draft(make(built, run_id="r2"))


def test_memory_store_last_by_status(built):
    st = MemoryStore()
    st.save_draft(make(built, run_id="r1"))
    st.set_status("r1", "applied")
    st.save_draft(make(built, run_id="r2"))
    assert st.last("ozon").run.run_id == "r2"
    assert st.last("ozon", KEEP).run.run_id == "r1"
    assert st.last("wb") is None
