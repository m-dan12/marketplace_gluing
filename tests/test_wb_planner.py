import tomllib
from collections import Counter
from pathlib import Path

from gluing.brands import brand_for
from gluing.model import Card
from gluing.parsing import parse_article
from gluing.rules import Context
from gluing.wb.planner import build_wb

ROOT = Path(__file__).resolve().parent.parent
PROFILE = tomllib.load(open(ROOT / "profiles" / "wb_proftex.toml", "rb"))
PROFILE["limits"].update(max_group=30, work_group=26, min_group=6)
CTX = Context(PROFILE, frozenset(), frozenset())


def card(design: int, key: str = "0-0-26", nm: int | None = None, stock: int = 5, cat="Наволочки", prefix="PT") -> Card:
    art = f"{prefix}{design}/{key}/1"
    info = parse_article(art)
    c = Card(article=art, nm_id=nm or design * 100 + abs(hash(key)) % 97, category=cat, prefix=info["prefix"], design=info["design"],
             key=info["key"], key_nums=info["key_nums"], stock_wb=stock)
    c.brand = brand_for(prefix) or ""
    return c


def run(cards, live=None, members=None, tails=False, up=6, down=None, prev=frozenset()):
    """up/down — пороги отделения размера (по умолчанию 6: размер отделяется, когда карточек не меньше min_group)."""
    prof = {**PROFILE, "rules": {**PROFILE["rules"], "merge_tails": tails, "size_split_up": up, "size_merge_down": down or up}}
    live = live or {}
    members = members or {}
    for c in cards:
        if c.nm_id in live:
            members.setdefault(live[c.nm_id], set()).add(c.nm_id)
    return build_wb(cards, live, members, prof, CTX, prev)


def sizes(res):
    return sorted(len(g.cards) for g in res.groups)


def test_one_size_per_group():
    cards = [card(i, "0-0-26") for i in range(1000, 1010)] + [card(i, "0-0-20") for i in range(2000, 2010)]
    res = run(cards)
    assert len(res.groups) == 2
    for g in res.groups:
        assert len({c.key for c in g.cards}) == 1


def test_split_equal_by_work_limit():
    res = run([card(i) for i in range(51)])
    assert sizes(res) == [25, 26]
    assert all(g.imt_id is None for g in res.groups)


def test_new_group_over_work_limit_splits_without_live_group():
    res = run([card(i) for i in range(28)])
    assert sizes(res) == [14, 14]


def test_existing_group_grows_up_to_hard_limit_instead_of_splitting():
    cards = [card(i) for i in range(1000, 1029)]               # 29 карточек, все уже в imt 7
    res = run(cards, live={c.nm_id: 7 for c in cards})
    assert len(res.groups) == 1 and res.groups[0].imt_id == 7 and res.groups[0].grown
    assert len(res.groups[0].cards) == 29


def test_existing_group_over_hard_limit_splits():
    cards = [card(i) for i in range(1000, 1032)]
    res = run(cards, live={c.nm_id: 7 for c in cards})
    assert sizes(res) == [16, 16] and 7 in {g.imt_id for g in res.groups}


def test_residual_inactive_cards_take_space():
    cards = [card(i) for i in range(1000, 1026)]               # 26 активных + 5 неактивных остаются в imt 7
    live = {c.nm_id: 7 for c in cards}
    members = {7: {c.nm_id for c in cards} | {900001, 900002, 900003, 900004, 900005}}
    res = build_wb(cards, live, members, PROFILE, CTX)
    assert max(len(g.cards) for g in res.groups) <= 25 and len(res.groups) == 2
    # с учётом остатка неактивных ни одна склейка не превышает жёсткий лимит
    for g in res.groups:
        if g.imt_id:
            assert len(g.cards) + len(members[7] - {c.nm_id for c in cards}) <= 30


def test_sticky_imt_inherited_by_majority_and_not_shared():
    a = [card(i) for i in range(1000, 1010)]
    b = [card(i) for i in range(2000, 2008)]                   # 18 карточек одного ключа, 10 в imt 1, 8 в imt 2
    live = {c.nm_id: 1 for c in a} | {c.nm_id: 2 for c in b}
    res = run(a + b, live=live)
    assert len(res.groups) == 1 and res.groups[0].imt_id == 1


def test_small_ruffle_branch_falls_back_to_generic_key():
    # рюши: последнее число ключа 13; менее min_group карточек -> уходят в обычный ключ
    ruffle = [card(i, "0-0-13") for i in range(1000, 1003)]
    res = run(ruffle)
    assert len(res.groups) == 1 and res.groups[0].bid == "key:0-0-13"
    assert any("Рюши" in k for k in res.moved)


def test_hotel_pillowcase_key_merges_into_regular_size():
    cards = [card(i, "0-0-26") for i in range(1000, 1004)] + [card(i, "0-0-260") for i in range(2000, 2004)]
    res = run(cards)
    assert len(res.groups) == 1 and len(res.groups[0].cards) == 8


def test_kinds():
    res = run([card(i) for i in range(1000, 1007)] + [card(i, "0-0-20") for i in range(2000, 2003)] + [card(5000, "0-0-22")])
    kinds = Counter(g.kind for g in res.groups)
    assert kinds == {"glue": 1, "small_glue": 1, "single_design": 1}


def test_deterministic():
    cards = [card(i) for i in range(60)]
    live = {c.nm_id: 1 + i % 3 for i, c in enumerate(cards)}
    a = run(cards, live=dict(live))
    b = run(list(reversed(cards)), live=dict(live))
    pick = lambda r: sorted((g.imt_id or 0, tuple(sorted(c.nm_id for c in g.cards))) for g in r.groups)
    assert pick(a) == pick(b)


def test_tails_of_neighbour_sizes_are_pooled():
    cards = [card(1000 + i, "0-0-20") for i in range(4)] + [card(2000 + i, "0-0-22") for i in range(3)] +             [card(3000 + i, "0-0-26") for i in range(8)]
    strict = run(cards)
    assert sorted(g.kind for g in strict.groups) == ["glue", "small_glue", "small_glue"]
    res = run(cards, tails=True)
    assert sorted(len(g.cards) for g in res.groups) == [7, 8] and all(g.kind == "glue" for g in res.groups)   # 4+3 хвостов в одной группе
    assert sum(g.mixed for g in res.groups) == 1


def test_lonely_tail_attaches_to_nearest_size_with_room():
    cards = [card(1000 + i, "0-0-26") for i in range(10)] + [card(3000, "0-0-20")]
    res = run(cards, tails=True)
    assert len(res.groups) == 1 and len(res.groups[0].cards) == 11 and res.groups[0].mixed


def test_rhomb_is_never_mixed():
    cards = [card(1000 + i, "44-0-0", cat="Пододеяльники") for i in range(3)] + [card(2000 + i, "54-0-0", cat="Пододеяльники") for i in range(3)]
    res = run(cards, tails=True)
    assert len(res.groups) == 2 and not any(g.mixed for g in res.groups)


def test_whole_category_brand_is_not_split_by_size():
    cards = [card(1000 + i, k, prefix="PC", cat="Наволочки") for i, k in enumerate(["0-0-20", "0-0-22", "0-0-24", "0-0-26", "0-0-20", "0-0-22", "0-0-24", "0-0-26"])]
    res = run(cards)                                           # слияние хвостов выключено: бренд делится только правилом «вся категория вместе»
    assert len(res.groups) == 1 and res.groups[0].mixed and res.groups[0].bid == "whole"


def test_tails_do_not_cross_category_or_brand():
    cards = [card(1000 + i, "0-0-20") for i in range(3)] + [card(2000 + i, "0-0-22", cat="Простыни") for i in range(3)] +             [card(3000 + i, "0-0-24", prefix="MT") for i in range(3)]
    res = run(cards, tails=True)
    assert len(res.groups) == 3 and not any(g.mixed for g in res.groups)


def test_carnival_and_kant_are_not_split_by_size():
    cards = [card(1000 + i, "104-116", cat="Карнавальные костюмы") for i in range(3)] +             [card(2000 + i, "116-128", cat="Карнавальные костюмы") for i in range(3)]
    for c in cards:
        c.title = "сарафан карнавальный"
    res = run(cards)
    assert len(res.groups) == 1 and res.groups[0].bid == "carnival:сарафаны" and res.groups[0].mixed
    kant = [card(3000 + i, f"0-0-{20 + i}") for i in range(7)]
    for c in kant:
        c.article += "KANT"
    res = run(kant)
    assert len(res.groups) == 1 and res.groups[0].bid == "kant"


def test_size_separates_only_above_threshold_otherwise_stays_in_parent():
    cards = [card(1000 + i, "0-0-26") for i in range(10)] + [card(2000 + i, "0-0-20") for i in range(7)] +             [card(3000 + i, "0-0-22") for i in range(3)]
    res = run(cards, tails=True, up=10, down=8)
    sizes_ = sorted(len(g.cards) for g in res.groups)
    assert sizes_ == [10, 10] and sum(g.mixed for g in res.groups) == 1       # размер 26 отдельно, 20+22 вместе как хвосты
    assert res.separated == {"key:0-0-26|Наволочки|Сказка|0-0-26"}


def test_size_hysteresis_keeps_yesterdays_separate_size_until_merge_down():
    cards = [card(1000 + i, "0-0-26") for i in range(8)] + [card(2000 + i, "0-0-20") for i in range(7)]
    fresh = run(cards, tails=True, up=10, down=8)                              # 8 < 10: вчерашнего решения нет, размеры сливаются
    assert len(fresh.groups) == 1 and fresh.groups[0].mixed
    held = run(cards, tails=True, up=10, down=8, prev={"key:0-0-26|Наволочки|Сказка|0-0-26"})   # вчера отделён, 8 >= 8: держим
    assert sorted(len(g.cards) for g in held.groups) == [7, 8] or len(held.groups) == 2
    dropped = run(cards[1:], tails=True, up=10, down=8, prev={"key:0-0-26|Наволочки|Сказка|0-0-26"})   # 7 < 8: сливаем
    assert len(dropped.groups) == 1
