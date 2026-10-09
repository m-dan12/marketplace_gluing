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
PROFILE = tomllib.load(open(ROOT / "profiles" / "ozon.toml", "rb"))

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


def test_rare_feature_wins_for_design():
    ds = designs_from([sku("PT5/4-13-17/1"), sku("PT5/4-13-26/1")])
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
