import tomllib
from collections import Counter
from pathlib import Path

from gluing.ozon.features import pick_feature, sku_feature
from gluing.ozon.loader import apply_roll, build_designs
from gluing.ozon.metrics import churn
from gluing.ozon.model import OzonSku
from gluing.ozon.tree import build
from gluing.parsing import parse_article

ROOT = Path(__file__).resolve().parent.parent
ACTUAL = tomllib.load(open(ROOT / "profiles" / "ozon.toml", "rb"))
# механика порогов и запасных веток проверяется при старых порогах 10/8 для всех веток; правило «признак отделяется всегда» отдельно (ACTUAL)
LOW_FEATURES = {**ACTUAL, "features": {"split_design": True, "split_up": 2, "merge_down": 1}}   # вариант: малые признаки отделяются от 2 дизайнов
# механика дерева проверяется без деления КПБ по размерности и без жёсткого отделения эко/детских (они отдельно: SIZED)
PROFILE = {**{k: v for k, v in ACTUAL.items() if k not in ("sizes", "hard_split")},
           "features": {k: v for k, v in ACTUAL["features"].items() if k not in ("split_up", "merge_down")}}
SIZED = {**PROFILE, "sizes": ACTUAL["sizes"], "hard_split": ACTUAL["hard_split"]}

SET = "Постельное белье > Комплект постельного белья"
PIL = "Постельное белье > Наволочка"
SHEET = "Постельное белье > Простыня"
DUV = "Постельное белье > Пододеяльник"
BAG = "Аксессуары > Сумка"


def sku(offer, cat=SET, title="", orders=0):
    p = parse_article(offer)
    return OzonSku(offer_id=offer, title=title, category=cat, stock=5, orders28=orders, prefix=p["prefix"],
                   design_no=p["design"], key=p["key"], key_nums=p["key_nums"])


def designs_from(skus, acc="skazka"):
    ds, unknown = build_designs(skus, [acc] * len(skus), PROFILE)
    assert unknown == []
    return ds


def n_designs(n, cat=SET, start=1000, key="4-13-26", pref="PT", orders=lambda i: 0):
    return [sku(f"{pref}{start + i}/{key}/1", cat, orders=orders(i)) for i in range(n)]


def run(skus, prev=None, acc="skazka"):
    return build(designs_from(skus, acc), PROFILE, prev)


# ---------- признаки ----------
def test_features_by_type():
    assert sku_feature("Комплект постельного белья", sku("PT1/4-13-17/1")) == "Рюши"
    assert sku_feature("Комплект постельного белья", sku("PT1/44-0-0/1")) == "Ромбы"
    assert sku_feature("Пододеяльник", sku("PC1/42-0-0/1kant", DUV)) == "Кант"
    assert sku_feature("Наволочка", sku("PT1/0-0-263/1", PIL)) == "Пуговицы"
    assert sku_feature("Наволочка", sku("PT1/0-0-17/1", PIL)) == "Рюши"
    assert sku_feature("Наволочка", sku("PT1/0-0-26/1", PIL, title="Наволочка с кружевом")) == "Кружево"
    assert sku_feature("Штора", sku("GR1/1-140175", "Шторы > Штора")) == "Уличные"


def test_sheet_elastic_is_not_a_feature():
    s = sku("PT1/0-16-0/0", SHEET, title="Простынь на резинке 160х200")
    assert sku_feature("Простыня", s) == "Обычные"


WHOLE = {**PROFILE, "features": {"split_design": False}}


def test_rare_feature_wins_for_design():
    skus = [sku("PT5/4-13-17/1"), sku("PT5/4-13-26/1")]
    ds, _ = build_designs(skus, ["skazka"] * 2, WHOLE)
    assert ds[0].features == Counter({"Рюши": 1, "Обычные": 1}) and pick_feature(ds[0]) == "Рюши"


# ---------- дерево ----------
def test_every_design_in_exactly_one_group_and_no_mixing():
    sk = n_designs(30) + n_designs(12, PIL, 2000, "0-0-17") + n_designs(25, SHEET, 3000, "0-16-0") \
        + n_designs(8, SET, 4000, "4-13-26", "MT")
    groups, _, _ = run(sk)
    seen = [d.uid for g in groups for d in g.designs]
    assert len(seen) == len(set(seen)) == 30 + 12 + 25 + 8
    for g in groups:
        assert len({(d.brand, d.typ) for d in g.designs}) == 1


def test_size_limits_for_regular_groups():
    groups, _, _ = run(n_designs(61))
    sizes = [len(g.designs) for g in groups if g.kind == "склейка"]
    assert max(sizes) <= 18 and min(sizes) >= 6 and sum(sizes) == 61


def test_ruffles_split_only_when_enough():
    base = n_designs(20)
    few = n_designs(7, SET, 5000, "4-13-17")      # меньше порога 10
    groups, log, _ = run(base + few)
    assert all(g.leaf == "Обычные" for g in groups)
    many = n_designs(12, SET, 5000, "4-13-17")    # ≥ 10
    groups, log, _ = run(base + many)
    assert {g.leaf for g in groups} == {"Обычные", "Рюши"}


def test_lace_falls_back_to_ruffles_then_to_ordinary():
    pil = n_designs(20, PIL, 1000, "0-0-26")
    ruffles = n_designs(6, PIL, 2000, "0-0-17")
    lace = [sku(f"PT{3000 + i}/0-0-26/1", PIL, title="Наволочка с кружевом") for i in range(5)]
    # рюши 6 + кружево 5 = 11 ≥ 10: кружево поглощено рюшами, вместе отделяются
    groups, _, _ = run(pil + ruffles + lace)
    assert {g.leaf for g in groups} == {"Обычные", "Рюши"}
    ruffle_designs = [d for g in groups if g.leaf == "Рюши" for d in g.designs]
    assert len(ruffle_designs) == 11
    # рюши 3 + кружево 3 = 6 < 10: всё уходит в обычные
    groups, _, _ = run(pil + n_designs(3, PIL, 2000, "0-0-17") + lace[:3])
    assert {g.leaf for g in groups} == {"Обычные"}


def test_children_then_eco_with_remainder_check():
    from gluing.lists import child_designs, eco_designs
    child_numbers = sorted(n for n in child_designs() if len(n) <= 4 and n not in eco_designs())[:10]
    eco_numbers = sorted(n for n in eco_designs() if len(n) == 4)[:10]
    base = [sku(f"PT{7000 + i}/4-13-26/1") for i in range(20)]
    kids = [sku(f"PT{n}/4-13-26/1") for n in child_numbers]
    eco = [sku(f"PT{n}/4-13-26/1") for n in eco_numbers]
    groups, _, _ = run(base + kids + eco)
    assert {g.leaf for g in groups} == {"Обычные › Детские", "Обычные › Эко", "Обычные"}
    # остаток меньше минимума: детские не отделяются
    groups, _, _ = run(kids + [sku(f"PT{7000 + i}/4-13-26/1") for i in range(4)])
    assert {g.leaf for g in groups} == {"Обычные"}


def test_small_node_becomes_small_group_and_single_is_not_glued():
    groups, _, _ = run(n_designs(3, BAG, 100, "70-45") + n_designs(1, "Аксессуары > Автогамак", 200, "130-147"))
    kinds = {g.typ: g.kind for g in groups}
    assert kinds["Сумка"] == "малая" and kinds["Автогамак"] == "одиночка"


def test_elastic_sheets_stay_in_one_branch():
    sk = []
    for i in range(12):
        sk.append(sku(f"PT{100 + i}/0-16-0/0", SHEET, title="Простынь на резинке 160х200"))
        sk.append(sku(f"PT{100 + i}/0-19-0/0", SHEET, title="Простыня классическая 200х215"))
    groups, _, _ = run(sk)
    assert {g.leaf for g in groups} == {"Обычные"}


# ---------- гистерезис и липкость ----------
def test_hysteresis_keeps_split_between_8_and_10():
    base = n_designs(20)
    r9 = n_designs(9, SET, 5000, "4-13-17")
    groups, _, state = run(base + r9)                   # 9 < 10 и вчера не было: не отделяем
    assert {g.leaf for g in groups} == {"Обычные"}
    groups, _, state = run(base + n_designs(11, SET, 5000, "4-13-17"))
    assert "Рюши" in {g.leaf for g in groups}
    # на следующий день рюш 9: вчера были отделены, значит остаются отдельно
    groups, _, state2 = run(base + r9, prev=state)
    assert "Рюши" in {g.leaf for g in groups}
    # рюш 7 < 8: сливаются обратно
    groups, _, _ = run(base + n_designs(7, SET, 5000, "4-13-17"), prev=state2)
    assert {g.leaf for g in groups} == {"Обычные"}


def test_sticky_assignment_keeps_designs_in_their_group():
    sk = n_designs(40, orders=lambda i: i)
    groups1, _, state1 = run(sk)
    new = sk + [sku("PT9999/4-13-26/1", orders=5)]       # добавился один дизайн
    groups2, _, state2 = run(new, prev=state1)
    ch = churn(state1, state2)
    assert ch["сменили склейку"] == 0
    # а без состояния веер по рангу перетасовал бы заметную часть
    _, _, state_nostate = run(new)
    assert churn(state1, state_nostate)["сменили склейку"] > 0


def test_sticky_adds_group_only_when_over_capacity_and_keeps_most():
    sk = n_designs(36)                                   # 2 группы по 18
    _, _, state1 = run(sk)
    groups, _, state2 = run(sk + n_designs(1, SET, 8000), prev=state1)   # 37 дизайнов: нужна 3-я группа
    sizes = sorted(len(g.designs) for g in groups)
    assert len(groups) == 3 and min(sizes) >= 6 and max(sizes) <= 18
    assert churn(state1, state2)["доля"] < 0.5


def test_unknown_prefix_is_reported_not_glued():
    ds, unknown = build_designs([sku("ZZ1/4-13-26/1")], ["skazka"], PROFILE)
    assert ds == [] and unknown == ["ZZ1/4-13-26/1"]


def test_single_design_with_several_skus_is_glued_together():
    one = [sku("PT6361/130-147/a", "Аксессуары > Автогамак", title="Накидка"),
           sku("PT6361/130-147/b", "Аксессуары > Автогамак", title="Накидка 2")]
    groups, _, _ = run(one)
    assert groups[0].kind == "один дизайн" and len(groups[0].designs[0].skus) == 2
    groups, _, _ = run(one[:1])
    assert groups[0].kind == "одиночка"


# ---------- рулоны тканей ----------
FAB = "Материал для рукоделия > Ткань"


def fab(offer, stock=0):
    s = sku(offer, FAB)
    s.stock = stock
    return s


def test_roll_cuts_are_meters_divided_by_cut_length():
    s = fab("PT5926/1-300220")
    apply_roll(s, "Ткань", {"PT5926": 80})
    assert (s.roll_m, s.cut_m, s.stock_roll) == (80, 3, 26)        # 80 м -> 26 отрезов по 3 м


def test_fabric_without_ozon_stock_is_active_thanks_to_roll():
    s = fab("PT5926/1-100220", stock=0)
    apply_roll(s, "Ткань", {"PT5926": 80})
    ds, _ = build_designs([s], ["skazka"], PROFILE)
    assert len(ds) == 1 and ds[0].skus[0].stock_roll == 80
    s2 = fab("PT5927/1-100220", stock=0)
    ds, _ = build_designs([s2], ["skazka"], PROFILE)               # нет рулона и нет остатка: не берём
    assert ds == []


def test_curtains_and_non_fabrics_get_no_roll():
    c = sku("PT5926/2-150200", "Шторы и карнавы > Штора")
    apply_roll(c, "Штора", {"PT5926": 80})
    assert c.stock_roll == 0
    odd = fab("PT5926/1-150200")                                    # ключ не вида 1-L00220
    apply_roll(odd, "Ткань", {"PT5926": 80})
    assert odd.stock_roll == 0


def test_small_roll_leaves_long_cuts_unavailable():
    s = fab("PT1/1-1100220")
    apply_roll(s, "Ткань", {"PT1": 10})                             # 10 м < отреза 11 м -> 0 отрезов
    assert s.stock_roll == 0


# ---------- деление дизайна по признаку ----------
def mixed_set(n_ruffle, n_plain):
    out = [sku(f"PT{1000 + i}/4-13-17/1") for i in range(n_ruffle)]
    out += [sku(f"PT{3000 + i}/4-13-26/1") for i in range(n_plain)]
    # один дизайн с обоими признаками
    out += [sku("PT9000/4-13-17/1"), sku("PT9000/4-13-26/1")]
    return out


def test_split_makes_design_units_by_feature():
    ds = designs_from([sku("PT5/4-13-17/1"), sku("PT5/4-13-26/1")])
    assert sorted((d.design, d.feature) for d in ds) == [("PT5", "Обычные"), ("PT5", "Рюши")]
    assert len({d.uid for d in ds}) == 2


def test_split_design_goes_to_two_groups_without_mixing():
    groups, _, _ = run(mixed_set(12, 12))
    where = {}
    for g in groups:
        for d in g.designs:
            for s in d.skus:
                assert (s.key_nums[-1] == "17") == (g.leaf.split(" › ")[0] == "Рюши"), (s.offer_id, g.leaf)
            where.setdefault(d.design, set()).add(g.key)
    assert len(where["PT9000"]) == 2


def test_small_branch_falls_back_and_design_counts_once():
    groups, _, _ = run(mixed_set(3, 12))                  # 3 + 1 рюшевых дизайна меньше порога 10: уходят в «Обычные»
    leaves = {g.leaf.split(" › ")[0] for g in groups}
    assert leaves == {"Обычные"}
    designs = [d.design for g in groups for d in g.designs]
    assert designs.count("PT9000") == 1                   # две части дизайна слились в одну единицу
    pt = next(d for g in groups for d in g.designs if d.design == "PT9000")
    assert len(pt.skus) == 2


def test_feature_always_defines_branch_even_when_small():
    curt = "Шторы > Штора"
    skus = [sku(f"PT{1000 + i}/1-140175", curt, title="Штора") for i in range(12)]
    skus += [sku(f"PT{2000 + i}/1-140175L", curt, title="Штора на липучках") for i in range(3)]
    groups, log, _ = build(designs_from(skus), LOW_FEATURES)
    leaves = {g.leaf: sum(len(g2.designs) for g2 in groups if g2.leaf == g.leaf) for g in groups}
    assert leaves == {"Обычные": 12, "На липучках": 3}


def test_single_design_feature_branch_goes_to_fallback():
    skus = [sku(f"PT{1000 + i}/4-13-26/1") for i in range(12)] + [sku("PT2000/4-13-17/1")]
    groups, _, _ = build(designs_from(skus), LOW_FEATURES)
    assert {g.leaf for g in groups} == {"Обычные"}


# ---------- близнецы (одинаковая подпись варианта) ----------
def with_sigs(ds, twin_pairs):
    for a, b in twin_pairs:
        for d in ds:
            if d.design in (a, b):
                d.sigs = frozenset({("twin", a, b)})
    return ds


def where(groups, design):
    return [g.key for g in groups for d in g.designs if d.design == design]


def test_twins_split_into_two_groups_when_one_group_would_do():
    ds = with_sigs(designs_from(n_designs(14)), [("PT1000", "PT1001")])
    groups, _, _ = build(ds, PROFILE)
    assert len(groups) == 2 and where(groups, "PT1000") != where(groups, "PT1001")
    assert all(len(g.designs) >= 6 for g in groups)


def test_twins_are_moved_between_existing_parts():
    ds = with_sigs(designs_from(n_designs(25)), [("PT1000", "PT1001")])
    groups, log, _ = build(ds, PROFILE)
    assert where(groups, "PT1000") != where(groups, "PT1001")
    assert all(6 <= len(g.designs) <= 18 for g in groups)
    assert not [r for r in log if r["level"] == "близнецы"]


def test_twins_in_small_group_are_reported_not_hidden():
    ds = with_sigs(designs_from(n_designs(4)), [("PT1000", "PT1001")])
    groups, log, _ = build(ds, PROFILE)
    assert where(groups, "PT1000") == where(groups, "PT1001")
    assert [r for r in log if r["level"] == "близнецы"]


# ---------- скатерти: новогодняя линейка по ключу ----------
CLOTH = "Полотенца и скатерти > Скатерть"


def cloth_set(n_ny, n_plain):
    out = [sku(f"PT{1000 + i}/2-120150", CLOTH) for i in range(n_ny)]
    out += [sku(f"PT{1000 + i}/2-180150", CLOTH) for i in range(n_ny)]            # размеры того же новогоднего дизайна
    out += [sku(f"PT{3000 + i}/2-140140", CLOTH) for i in range(n_plain)]
    return out


def test_tablecloth_key_150_is_own_branch():
    groups, _, _ = build(designs_from(cloth_set(7, 12)), ACTUAL)
    assert {g.leaf: len(g.designs) for g in groups} == {"Новогодние": 7, "Обычные": 12}
    for g in groups:
        for d in g.designs:
            assert all(s.key.endswith("150") == (g.leaf == "Новогодние") for s in d.skus)


def test_tablecloth_few_new_year_designs_fall_back_when_single():
    groups, _, _ = build(designs_from(cloth_set(1, 12)), ACTUAL)
    assert {g.leaf for g in groups} == {"Обычные"}                                # один дизайн: отдельной ветки нет


# ---------- признаки из профиля (регулярные выражения) ----------
def test_new_feature_is_added_by_profile_row_without_code():
    from gluing.ozon.features import Rules
    feats = {**ACTUAL["features"], "types": {**ACTUAL["features"]["types"], "Полотенце": {
        "default": "Обычные", "fallback": {"Банные": "Обычные"},
        "rules": [{"name": "Банные", "key": r"^3-\d+$", "title": "банн"}]}}}
    rules = Rules(feats)
    s = sku("PT1/3-70140/1", "Полотенца > Полотенце", title="Полотенце банное")
    assert rules.feature("Полотенце", s) == "Банные"
    assert rules.feature("Полотенце", sku("PT1/3-70140/1", "Полотенца > Полотенце", title="Полотенце кухонное")) == "Обычные"
    assert rules.feature("Неизвестный тип", s) == "—"


def test_rule_conditions_are_and_and_first_match_wins():
    from gluing.ozon.features import Rules
    r = Rules({"types": {"Т": {"default": "d", "rules": [
        {"name": "a", "key": "^1", "title": "x"}, {"name": "b", "key": "^1"}]}}})
    assert r.feature("Т", sku("PT1/1-2-3/1", "К > Т", title="x")) == "a"
    assert r.feature("Т", sku("PT1/1-2-3/1", "К > Т", title="y")) == "b"
    assert r.feature("Т", sku("PT1/2-2-3/1", "К > Т", title="x")) == "d"


def test_bad_rules_are_rejected():
    import pytest
    from gluing.ozon.features import Rules
    with pytest.raises(ValueError):
        Rules({"types": {"Т": {"default": "d", "rules": [{"name": "a"}]}}})                 # нет условий
    with pytest.raises(ValueError):
        Rules({"types": {"Т": {"default": "d", "rules": [{"name": "a", "ключ": "1"}]}}})    # неизвестное поле


def test_profile_rules_match_documented_cases():
    from gluing.ozon.features import Rules
    r = Rules.from_profile(ACTUAL)
    assert r.feature("Комплект постельного белья", sku("PT1/44-0-0/1")) == "Ромбы"
    assert r.feature("Комплект постельного белья", sku("PT1/4-13-17/1")) == "Рюши"
    assert r.feature("Комплект постельного белья", sku("PT1/4-13-26/1")) == "Обычные"
    assert r.feature("Скатерть", sku("PT1/2-120150", CLOTH)) == "Новогодние"
    assert r.feature("Скатерть", sku("PT1/2-140140", CLOTH)) == "Обычные"


# ---------- ветки признака из одного дизайна и одиночный остаток ----------
def duv(plain, kant, start=1000):
    return ([sku(f"PT{start + i}/5-0-0/1", DUV) for i in range(plain)]
            + [sku(f"PT{start + 500 + i}/42-0-0/1kant", DUV) for i in range(kant)])


def test_feature_branch_is_not_kept_when_only_one_design_remains():
    _, _, day1 = build(designs_from(duv(6, 2)), ACTUAL)
    assert any(g.leaf == "Кант" for g in build(designs_from(duv(6, 2)), ACTUAL)[0])
    groups, _, _ = build(designs_from(duv(6, 1)), ACTUAL, day1)         # вчера отделён, сегодня остался 1 дизайн
    assert {g.leaf for g in groups} == {"Обычные"} and all(g.kind != "один дизайн" for g in groups)


def test_separating_feature_never_leaves_a_single_design_remainder():
    groups, _, _ = build(designs_from(duv(1, 2)), ACTUAL)             # 3 дизайна: кант 2 + обычный 1
    assert {g.leaf for g in groups} == {"Обычные"}
    assert [len(g.designs) for g in groups] == [3] and groups[0].kind == "малая"


# ---------- гистерезис по остатку ----------
def stock_sku(offer, stock, out_days):
    s = sku(offer, SET)
    s.stock, s.out_days = stock, out_days
    return s


def test_grace_period_keeps_recently_sold_out_sku_in_plan():
    prof = {**PROFILE, "stock": {"min_total": 2, "grace_days": 14}}
    skus = [stock_sku("PT1/4-13-26/1", 0, 5), stock_sku("PT2/4-13-26/1", 0, 14), stock_sku("PT3/4-13-26/1", 0, 15),
            stock_sku("PT4/4-13-26/1", 0, None), stock_sku("PT5/4-13-26/1", 9, 0)]
    ds, _ = build_designs(skus, ["skazka"] * 5, prof)
    assert sorted(d.design for d in ds) == ["PT1", "PT2", "PT5"]            # 15 дней и «неизвестно» не удерживаются


def test_no_grace_by_default():
    prof = {k: v for k, v in PROFILE.items() if k != "stock"}
    ds, _ = build_designs([stock_sku("PT1/4-13-26/1", 0, 1)], ["skazka"], prof)
    assert ds == []


# ---------- КПБ: размерность, эко и детские ----------
def run_sized(skus, acc="skazka", prev=None):
    ds, unknown = build_designs(skus, [acc] * len(skus), SIZED)
    assert unknown == []
    return build(ds, SIZED, prev)


def _plain(n, start=70000, key="4-13-26", pref="PT"):
    from gluing.lists import child_designs, eco_designs
    bad = eco_designs() | child_designs()
    out, i = [], 0
    while len(out) < n:
        if str(start + i) not in bad:
            out.append(sku(f"{pref}{start + i}/{key}/1"))
        i += 1
    return out


def test_kpb_group_has_one_size_and_design_is_split_between_sizes():
    a, b = _plain(8, 70000, "4-13-26"), _plain(8, 70100, "5-13-26")
    both = [sku("PT70000/5-13-26/1")]                      # тот же дизайн, что в a[0], но размер 2-сп
    groups, log, _ = run_sized(a + b + both)
    for g in groups:
        sizes = {sk.key.split("-")[0] for d in g.designs for sk in d.skus}
        assert len(sizes) == 1, (g.leaf, sizes)
    leaves = sorted({g.leaf for g in groups})
    assert leaves == ["Обычные › 1,5-сп", "Обычные › 2-сп"]
    assert sum(1 for g in groups for d in g.designs if d.design == "PT70000") == 2
    uids = [d.uid for g in groups for d in g.designs]
    assert len(uids) == len(set(uids))


def test_kpb_size_by_two_digit_key_and_unknown_lead():
    groups, _, _ = run_sized(_plain(6, 70000, "42-0-0") + _plain(6, 70100, "4-13-26") + _plain(6, 70200, "7-13-26"))
    assert {g.leaf for g in groups} == {"Обычные › 1,5-сп", "Обычные › размер 7"}


def test_kpb_mix_brands_keep_all_sizes_together():
    sk = _plain(7, 70000, "4-13-26", "MT") + _plain(7, 70000, "5-13-26", "MT")
    groups, _, _ = run_sized(sk)
    assert len(groups) == 1 and groups[0].leaf == "Обычные" and len(groups[0].designs) == 7


def test_other_bed_types_are_not_split_by_size():
    sk = n_designs(8, PIL, 70000, "0-0-26") + n_designs(8, PIL, 70100, "4-0-26")
    groups, _, _ = run_sized(sk)
    assert all("›" not in g.leaf for g in groups)


def test_kpb_eco_always_separate_and_kids_are_defined_by_size_not_by_list():
    from gluing.lists import child_designs, eco_designs
    ecos = sorted(d for d in eco_designs() if len(d) == 4)[:3]
    listed_kids = sorted(d for d in child_designs() if len(d) <= 4 and d not in eco_designs())[:2]
    sk = _plain(12) + [sku(f"PT{e}/4-13-26/1") for e in ecos] + [sku(f"PT{k}/4-13-26/1") for k in listed_kids]         + _plain(3, 70500, "2-12-26") + _plain(2, 70600, "1-12-24")
    groups, log, _ = run_sized(sk)
    by = {g.leaf: len(g.designs) for g in groups}
    # дизайны из списка «детских» во взрослом размере остаются обычными; детские — только размеры 1 и 2
    assert by == {"Обычные › 1,5-сп": 14, "Обычные › Эко › 1,5-сп": 3, "Обычные › Детский 1,5-сп": 3, "Обычные › Детский 1": 2}, by
    # без жёсткого правила (прежняя механика) те же дизайны остались бы в одной ветке
    groups_old, _, _ = run(sk)
    assert len({g.leaf for g in groups_old}) == 1


def test_mix_brand_keeps_child_list_because_it_has_no_size_units():
    from gluing.lists import child_designs
    kids = sorted(d for d in child_designs() if len(d) <= 4)[:3]
    sk = _plain(7, 70000, "4-13-26", "MT") + [sku(f"MT{k}/4-13-26/1") for k in kids]
    groups, _, _ = run_sized(sk)
    assert {g.leaf: len(g.designs) for g in groups} == {"Обычные": 7, "Обычные › Детские": 3}
