import tomllib
from pathlib import Path

from gluing.brands import brand_for
from gluing.model import Card
from gluing.parsing import parse_article
from recommend.wb import recommend

ROOT = Path(__file__).resolve().parent.parent
PROF = tomllib.load(open(ROOT / "profiles" / "wb_proftex.toml", "rb"))


def card(nm: int, design: int, key="0-0-26", cat="Наволочки", prefix="PT", orders=0, stock=5) -> Card:
    art = f"{prefix}{design}/{key}/1"
    info = parse_article(art)
    return Card(article=art, nm_id=nm, category=cat, brand=brand_for(prefix) or "", prefix=prefix, design=info["design"], key=info["key"],
                key_nums=info["key_nums"], orders30=orders, stock_wb=stock)


def world(n=60) -> list[Card]:
    return [card(1000 + i, 1000 + i // 3, key=["0-0-26", "1-500-220", "0-0-17"][i % 3], orders=i % 7, stock=1 + i % 4,
                 cat=["Наволочки", "Простыни", "Постельное белье"][i % 3]) for i in range(n)]


def test_up_to_twenty_unique_without_self_and_same_imt():
    cards = world()
    imt = {c.nm_id: c.nm_id // 10 for c in cards}
    res = recommend(cards, imt, PROF)
    for t, items in res.recs.items():
        nms = [nm for nm, _ in items]
        assert len(nms) == len(set(nms)) == 20 and t not in nms
        assert all(imt[nm] != imt[t] for nm in nms)


def test_same_design_comes_first_and_other_slots_skip_it():
    cards = world()
    res = recommend(cards, {}, PROF)
    by = {c.nm_id: c for c in cards}
    t = cards[0]
    items = res.recs[t.nm_id]
    assert {by[nm].design for nm, s in items if s == "design"} == {t.design}
    assert all(by[nm].design != t.design for nm, s in items if s not in ("design", "fill"))


def test_mechta_only_with_mechta_and_skazka_mixes_with_anna_maria():
    cards = [card(1000 + i, 100 + i, prefix="MT") for i in range(25)] + [card(2000 + i, 200 + i, prefix=["PT", "PC", "AM"][i % 3]) for i in range(30)]
    by = {c.nm_id: c for c in cards}
    res = recommend(cards, {}, PROF)
    assert all(by[nm].brand == "Мечта" for nm, _ in res.recs[1000])
    assert {by[nm].brand for nm, _ in res.recs[2000]} == {"Сказка", "Сказка Сатин", "Анна Мария"}


def test_eco_only_with_eco():
    cards = [card(1000 + i, 5928 + i if i < 25 else 300 + i) for i in range(50)]
    by = {c.nm_id: c for c in cards}
    res = recommend(cards, {}, PROF, frozenset(str(5928 + i) for i in range(25)))
    assert all(int(by[nm].design) >= 5928 for nm, _ in res.recs[1000])
    assert all(int(by[nm].design) < 5928 for nm, _ in res.recs[1030])


def test_rotation_spreads_tops():
    res = recommend(world(90), {}, PROF)
    tops = {nm for items in res.recs.values() for nm, s in items if s == "top"}
    assert len(tops) > 8                        # слот хитов не сводится к одним и тем же 4 карточкам


def test_no_stock_not_recommended():
    cards = world()
    cards[5].stock_wb = 0
    res = recommend(cards, {}, PROF)
    assert cards[5].nm_id not in res.recs
    assert all(nm != cards[5].nm_id for v in res.recs.values() for nm, _ in v)
