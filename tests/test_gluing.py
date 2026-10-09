import tomllib
from pathlib import Path

import pytest

from gluing.brands import brand_for
from gluing.builder import build, split_equal
from gluing.checks import hard_violations
from gluing.model import Card
from gluing.parsing import design_candidates, is_kant, parse_article
from gluing.rules import Context, assign

ROOT = Path(__file__).resolve().parent.parent
PROFILE = tomllib.load(open(ROOT / "profiles" / "wb_proftex.toml", "rb"))
ECO = frozenset({"5929", "6371"})
CHILD = frozenset({"111", "3411"})
CTX = Context(PROFILE, ECO, CHILD)
ORDER = PROFILE["rules"]["order"]


def card(article: str, category="Наволочки", brand=None, stock=5, orders=0, title="") -> Card:
    info = parse_article(article)
    c = Card(article=article, category=category, title=title, prefix=info["prefix"], design=info["design"],
             key=info["key"], key_nums=info["key_nums"], stock_wb=stock, orders30=orders)
    c.brand = brand or brand_for(c.prefix) or ""
    return c


def rule_of(c: Card) -> str:
    return assign(c, CTX, ORDER).rule


# --- разбор артикула и бренда ---
def test_parse_bedding():
    p = parse_article("PT5948/0-0-20/1")
    assert (p["prefix"], p["design"], p["key"], p["key_nums"]) == ("PT", "5948", "0-0-20", ("0", "0", "20"))


def test_parse_other_category_key_and_suffix_letters():
    assert parse_article("PT3143/2-150200P")["key"] == "2-150200"


def test_parse_companion_design():
    p = parse_article("PT63706371/5-16-28/1")
    assert p["design"] == "63706371" and design_candidates(p["design"]) == ["63706371", "6370"]


def test_cyrillic_typo_prefix():
    assert parse_article("РТ1234/0-0-26/1")["prefix"] == "PT"


@pytest.mark.parametrize("prefix,brand", [("PT", "Сказка"), ("GR", "Сказка"), ("OK", "Сказка"), ("PC", "Сказка Сатин"),
                                          ("MT", "Мечта"), ("AM", "Анна Мария"), ("PA", "Анна Мария"),
                                          ("MY", "Milky Garden"), ("", "Timeless")])
def test_brand_by_prefix(prefix, brand):
    assert brand_for(prefix) == brand


def test_unknown_prefix_is_not_guessed():
    assert brand_for("ZZ") is None


def test_kant():
    assert is_kant("PT123KANT/0-0-26/1") and not is_kant("PT123/0-0-26/1")


# --- правила ---
def test_eco_design_in_bedding():
    assert rule_of(card("PT5929/0-0-22/1")) == "ЭКО"


def test_eco_companion_by_first_four_digits():
    assert rule_of(card("PT59295930/0-0-22/1")) == "ЭКО"


def test_eco_not_applied_to_other_categories():
    assert rule_of(card("PT5929/1-150200", category="Шторы интерьерные")).startswith("Ключ")


def test_eco_not_for_anna_maria():
    assert rule_of(card("AM5929/0-0-22/1")) != "ЭКО"


def test_child_design():
    assert rule_of(card("PT111/0-0-26/1")) == "Детские"


def test_child_not_for_fabric():
    c = card("PT111/1-500220", category="Ткани для рукоделия")
    assert rule_of(c) == "Ключ 1-500220"


def test_button_pillowcase_ignores_eco():
    assert rule_of(card("PT5929/0-0-263/1")) == "Наволочки 0-0-263"


def test_hotel_pillowcase_joins_0_0_26():
    assert assign(card("PT2000/0-0-260/1"), CTX, ORDER).bid == "key:0-0-26"


def test_ruffle_only_for_skazka():
    assert rule_of(card("PT2000/0-0-17/1")) == "Рюши — наволочки"
    assert rule_of(card("MT2000/0-0-17/0PS")).startswith("Ключ")


def test_eco_ruffle():
    assert rule_of(card("PT5929/0-0-17/1")) == "ЭКО — рюши — наволочки"


def test_rhomb_stays_with_own_key():
    assert rule_of(card("PT2000/44-0-0/1", category="Пододеяльники")) == "РОМБЫ Пододеяльники — только 44-0-0"


def test_whole_category_brands():
    assert "категория максимально вместе" in rule_of(card("PC2000/0-0-26/1"))
    assert "вся категория вместе" in rule_of(card("AM2000/0-0-26/1"))


def test_carnival_by_title():
    c = card("PT9/1-1", category="Карнавальные костюмы", title="Костюм Сарафан")
    assert rule_of(c) == "Карнавал — сарафаны"


# --- сборка ---
def test_split_equal_and_spread():
    cards = [card(f"PT{i}/0-0-26/1", orders=100 - i) for i in range(65)]
    parts = split_equal(cards, 30)
    assert [len(p) for p in parts] == [22, 22, 21]
    assert all(any(c.orders30 > 90 for c in p) for p in parts)     # хиты в каждой части


def test_no_mixing_brands_or_categories_and_limit():
    cards = []
    for brand, pref in (("Сказка", "PT"), ("Мечта", "MT")):
        for n, cat in enumerate(("Наволочки", "Простыни")):
            for i in range(40):
                cards.append(card(f"{pref}{3000 + n * 100 + i}/0-0-26/1", category=cat))
    groups = build(cards, PROFILE, CTX)
    assert hard_violations(groups, PROFILE) == []
    assert sum(len(g.cards) for g in groups) == len(cards)


def test_small_neighbor_keys_merge_within_limit():
    cards = [card(f"PT{i}/0-0-26/1") for i in range(10)] + [card(f"PT{100 + i}/0-0-28/1") for i in range(8)]
    groups = build(cards, PROFILE, CTX)
    assert len(groups) == 1 and groups[0].rule == "Ключ 0-0-26 + Ключ 0-0-28"


def test_every_card_in_exactly_one_group():
    cards = [card(f"PT{i}/0-0-{20 + i % 5}/1") for i in range(100)]
    groups = build(cards, PROFILE, CTX)
    ids = [c.article for g in groups for c in g.cards]
    assert sorted(ids) == sorted(c.article for c in cards)


# --- CP-SAT ---
from gluing import solver as cpsat  # noqa: E402


@pytest.mark.skipif(not cpsat.available(), reason="ortools не установлен")
def test_cpsat_respects_hard_rules_and_is_deterministic():
    cards = []
    for k, n in (("0-0-20", 7), ("0-0-21", 5), ("0-0-26", 45), ("0-0-28", 12)):
        cards += [card(f"PT{k.replace('-', '')}{i}/{k}/1", orders=i) for i in range(n)]
    prof = {**PROFILE, "solver": {"deterministic_limit": 5}}
    g1 = build(cards, prof, CTX, solver="cpsat")
    g2 = build(cards, prof, CTX, solver="cpsat")
    assert hard_violations(g1, prof) == []
    assert sum(len(g.cards) for g in g1) == len(cards)
    assert [[c.article for c in g.cards] for g in g1] == [[c.article for c in g.cards] for g in g2]


@pytest.mark.skipif(not cpsat.available(), reason="ortools не установлен")
def test_cpsat_merges_tiny_neighbor_keys_instead_of_leaving_small_groups():
    cards = [card(f"PT{i}/0-0-20/1") for i in range(4)] + [card(f"PT{50 + i}/0-0-21/1") for i in range(4)]
    prof = {**PROFILE, "solver": {"deterministic_limit": 5}}
    groups = build(cards, prof, CTX, solver="cpsat")
    assert len(groups) == 1 and len(groups[0].cards) == 8
