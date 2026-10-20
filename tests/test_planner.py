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


def test_live_name_wins_over_previous_plan():
    ids = assign_ids([("k1", ["a", "b"])], prev_ids={"k1": "g_old"}, live_models={"a": "Model", "b": "Model"})
    assert ids["k1"] == "Model"                               # реальное имя на площадке важнее вчерашнего плана


def test_prev_id_used_when_no_live_name():
    assert assign_ids([("k1", ["a", "b"])], prev_ids={"k1": "g_old"})["k1"] == "g_old"


def test_inherit_live_model_name_when_half_match():
    ids = assign_ids([("k1", ["a", "b", "c", "d"])], live_models={"a": "M1", "b": "M1", "c": "M2"})
    assert ids["k1"] == "M1"                                  # 2 из 4 = 50%


def test_no_inherit_below_half_and_name_not_reused():
    ids = assign_ids([("k1", ["a", "b", "c", "d"]), ("k2", ["e", "f"])],
                     live_models={"a": "M1", "e": "M1", "f": "M1"}, min_overlap=0.5)
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


def test_twin_proposals_use_ordinal_and_skip_taken_names():
    from gluing.ozon.model import Design, OGroup, OzonSku
    from planner.plan import twins_check

    def d(code, offer):
        s = OzonSku(offer_id=offer, title="", category="", stock=5, orders28=0, prefix="PT", design_no=code[2:], key="1", key_nums=())
        return Design(account="skazka", brand="Сказка", typ="Комплект", design=code, digits=code[2:], skus=[s])

    g = OGroup(key="k", account="skazka", brand="Сказка", typ="Комплект", leaf="Обычные", idx=1,
               designs=[d("PT343", "a"), d("PT344", "b"), d("PT345", "c"), d("PT400", "x")], kind="склейка")
    cat = (1, 2)
    info = lambda color: {"cat": cat, "name": "", "attrs": {"10097": [color], "6771": ["160x200"]}}
    attrs = {("skazka", "a"): info("Бисер"), ("skazka", "b"): info("Бисер"), ("skazka", "c"): info("Бисер"),
             ("skazka", "x"): info("Бисер 2")}                 # «Бисер 2» уже занято в группе
    aspects = {cat: {10097: "Название цвета", 6771: "Размер простыни"}}
    names = {10097: "Название цвета", 6771: "Размер простыни"}
    # x отличается подписью? нет: все цвета разные кроме a/b/c — x тоже «Бисер 2» и не близнец
    tw = twins_check([g], attrs, aspects, names)
    assert len(tw) == 1 and tw[0]["designs"] == ["PT343", "PT344", "PT345"]
    assert [p["new_value"] for p in tw[0]["proposals"]] == ["Бисер 3", "Бисер 4"]


def _cloth_groups(extra_a, extra_b):
    from gluing.ozon.model import Design, OGroup, OzonSku
    from planner.plan import twins_check

    def d(code, offer):
        s = OzonSku(offer_id=offer, title="", category="", stock=5, orders28=0, prefix="PT", design_no=code[2:], key="1", key_nums=())
        return Design(account="skazka", brand="Сказка", typ="Комплект", design=code, digits=code[2:], skus=[s])

    g = OGroup(key="k", account="skazka", brand="Сказка", typ="Комплект", leaf="Обычные", idx=1,
               designs=[d("PT343", "a"), d("PT344", "b")], kind="склейка")
    cat = (1, 2)
    mk = lambda extra: {"cat": cat, "name": "", "attrs": {"10097": ["Бисер"], "6771": ["160x200"], **extra}}
    attrs = {("skazka", "a"): mk(extra_a), ("skazka", "b"): mk(extra_b)}
    aspects = {cat: {10097: "Название цвета", 6771: "Размер простыни"}}
    names = {10097: "Название цвета", 6771: "Размер простыни", 500: "Тип застёжки", 501: "Аннотация"}
    return twins_check([g], attrs, aspects, names)


def test_twin_proposal_uses_distinguishing_attribute_first():
    tw = _cloth_groups({"500": ["на молнии"]}, {"500": ["на пуговицах"]})
    assert tw[0]["proposals"][0]["new_value"] == "Бисер, на пуговицах"
    assert tw[0]["proposals"][0]["source"] == "Тип застёжки"


def test_twin_proposal_ignores_service_attributes_and_falls_back_to_ordinal():
    tw = _cloth_groups({"501": ["один текст"]}, {"501": ["другой текст"]})      # аннотация в служебных
    assert tw[0]["proposals"][0]["new_value"] == "Бисер 2" and tw[0]["proposals"][0]["source"] == "порядковый номер"


def test_live_names_go_to_max_overlap_pairs_not_biggest_group():
    # M1 у a,b,c (в группе big) и у d (в small); M2 у e. Максимум общих артикулов у пары (big, M1) — 3.
    groups = [("big", ["a", "b", "c", "x"]), ("small", ["d", "e"])]
    live = {"a": "M1", "b": "M1", "c": "M1", "d": "M1", "e": "M2"}
    ids = assign_ids(groups, live_models=live, min_overlap=0.2)
    assert ids["big"] == "M1" and ids["small"] == "M2"


def test_min_overlap_floor_prevents_weak_reuse():
    ids = assign_ids([("k", ["a", "b", "c", "d", "e"])], live_models={"a": "M1"}, min_overlap=0.5)
    assert ids["k"] == new_id("k")
    assert assign_ids([("k", ["a", "b", "c", "d", "e"])], live_models={"a": "M1"}, min_overlap=0.1)["k"] == "M1"


def test_twins_inside_one_design_get_proposals_per_sku():
    from gluing.ozon.model import Design, OGroup, OzonSku
    from planner.plan import twins_check

    def sk(o):
        return OzonSku(offer_id=o, title="", category="", stock=5, orders28=0, prefix="PT", design_no="4360", key="1", key_nums=())

    g = OGroup(key="k", account="skazka", brand="Сказка", typ="Комплект", leaf="Обычные", idx=1, kind="склейка",
               designs=[Design(account="skazka", brand="Сказка", typ="Комплект", design="PT4360", digits="4360", skus=[sk("a"), sk("b"), sk("c")])])
    cat = (1, 2)
    mk = {"cat": cat, "name": "", "attrs": {"10097": ["Сердечки"], "6771": ["160x200"]}}
    attrs = {("skazka", o): mk for o in "abc"}
    aspects = {cat: {10097: "Название цвета", 6771: "Размер простыни"}}
    tw = twins_check([g], attrs, aspects, {10097: "Название цвета", 6771: "Размер простыни"})
    assert tw[0]["same_design"] and [(p["offers"], p["new_value"]) for p in tw[0]["proposals"]] == [(["b"], "Сердечки 2"), (["c"], "Сердечки 3")]


def _kit(code, offers, orders=0):
    from gluing.ozon.model import Design, OzonSku
    skus = [OzonSku(offer_id=o, title="", category="", stock=5, orders28=orders, prefix="PT", design_no=code[2:], key="1", key_nums=())
            for o in offers]
    return Design(account="skazka", brand="Сказка", typ="Комплект", design=code, digits=code[2:], skus=skus)


def test_unify_colors_takes_the_most_common_value_and_fills_empty():
    from gluing.ozon.model import OGroup
    from planner.plan import unify_colors, with_colors
    g = OGroup(key="k", account="skazka", brand="Сказка", typ="Комплект", leaf="Обычные", idx=1, kind="склейка",
               designs=[_kit("PT1", ["a", "b", "c"]), _kit("PT2", ["d", "e"]), _kit("PT3", ["f", "g"])])
    cat = (1, 2)
    mk = lambda c: {"cat": cat, "name": "", "attrs": {"10097": [c]} if c else {}}
    attrs = {("skazka", o): mk(c) for o, c in zip("abcdefg", ["Classic", "Классика", "Classic", "Роза", "Роза", "", ""])}
    aspects = {cat: {10097: "Название цвета"}}
    rec, fixes = unify_colors([g], attrs, aspects)
    assert [(r["design"], r["value"], r["offers"]) for r in rec] == [("PT1", "Classic", {"b": "Классика"})]   # PT3 без значений не трогаем
    assert fixes == {("skazka", "b"): "Classic"}
    assert with_colors(attrs, fixes)[("skazka", "b")]["attrs"]["10097"] == ["Classic"]
    assert attrs[("skazka", "b")]["attrs"]["10097"] == ["Классика"]               # исходный снимок цел


def test_unify_colors_skips_categories_without_color_aspect():
    from gluing.ozon.model import OGroup
    from planner.plan import unify_colors
    g = OGroup(key="k", account="skazka", brand="Сказка", typ="Комплект", leaf="Обычные", idx=1, kind="склейка",
               designs=[_kit("PT1", ["a", "b"])])
    attrs = {("skazka", "a"): {"cat": (1, 2), "name": "", "attrs": {"10097": ["X"]}},
             ("skazka", "b"): {"cat": (1, 2), "name": "", "attrs": {"10097": ["Y"]}}}
    assert unify_colors([g], attrs, {(1, 2): {6771: "Размер"}}) == ([], {})


def test_twin_rename_covers_the_whole_design_not_only_clashing_offers():
    from gluing.ozon.model import OGroup
    from planner.plan import twins_check
    g = OGroup(key="k", account="skazka", brand="Сказка", typ="Комплект", leaf="Обычные", idx=1, kind="склейка",
               designs=[_kit("PT1", ["a"]), _kit("PT2", ["b", "c"])])
    cat = (1, 2)
    mk = lambda size: {"cat": cat, "name": "", "attrs": {"10097": ["Бисер"], "6771": [size]}}
    attrs = {("skazka", "a"): mk("160x200"), ("skazka", "b"): mk("160x200"), ("skazka", "c"): mk("180x200")}   # c не близнец, но того же дизайна
    aspects = {cat: {10097: "Название цвета", 6771: "Размер простыни"}}
    tw = twins_check([g], attrs, aspects, {10097: "Название цвета", 6771: "Размер простыни"})
    assert tw[0]["proposals"][0]["offers"] == ["b", "c"] and tw[0]["proposals"][0]["new_value"] == "Бисер 2"


def test_unify_colors_prefers_names_over_color_lists_and_offer_codes():
    from gluing.ozon.model import OGroup
    from planner.plan import is_color_name, unify_colors
    assert is_color_name("Nicol") and is_color_name("Бисер/Бисер (компаньон)") and is_color_name("Классика белая")
    assert not is_color_name("белый; серо-зеленый") and not is_color_name("MY3761/5-20-26/1") and not is_color_name("3526")
    g = OGroup(key="k", account="skazka", brand="Сказка", typ="Комплект", leaf="Обычные", idx=1, kind="склейка",
               designs=[_kit("PT1", ["a", "b", "c"]), _kit("PT2", ["d", "e"])])
    cat = (1, 2)
    mk = lambda c: {"cat": cat, "name": "", "attrs": {"10097": [c]}}
    # у PT1 список цветов в большинстве, но есть одно настоящее название; у PT2 только списки и артикул: не трогаем
    attrs = {("skazka", o): mk(c) for o, c in zip("abcde", ["белый;серый", "белый;серый", "Nicol", "бежевый; голубой", "PT2/5-1-1/1"])}
    rec, fixes = unify_colors([g], attrs, {cat: {10097: "Название цвета"}})
    assert [(r["design"], r["value"]) for r in rec] == [("PT1", "Nicol")] and set(fixes) == {("skazka", "a"), ("skazka", "b")}


def test_twin_proposal_does_not_use_seller_code_as_colour_tail():
    from planner.twins import distinguishing_value
    cat = (1, 2)
    attrs = {("skazka", "a"): {"cat": cat, "name": "", "attrs": {"9999": ["PT343/4-1-1/1"]}},
             ("skazka", "b"): {"cat": cat, "name": "", "attrs": {"9999": ["PT344/4-1-1/1"]}}}
    names = {9999: "Код продавца"}
    assert distinguishing_value("skazka", ["a"], ["b"], attrs, {cat: {}}, names) == ("", "")
