import tomllib
from pathlib import Path

from recommend.report import build_report, summary
from tests.test_recommend import card
from tools import wb_recommend as wr

ROOT = Path(__file__).resolve().parent.parent
PROF = tomllib.load(open(ROOT / "profiles" / "wb_proftex.toml", "rb"))


def world(n=30):
    return {1000 + i: card(1000 + i, 100 + i) for i in range(n)}


def lists(cards, k=20):
    ids = sorted(cards)
    return {t: [x for x in ids if x != t][:k] for t in ids}


def test_clean_state_is_ok():
    cards = world()
    rep = build_report(lists(cards), cards, {}, PROF, frozenset())
    assert rep.ok and rep.targets == 30 and not rep.short
    level, title, text = summary("milky_garden", rep, {"changed": 5, "matched": 5})
    assert level == "ok" and "всё в порядке" in title and "✗" not in text


def test_card_without_recommendations_and_without_stock_are_flagged():
    cards = world()
    actual = lists(cards)
    actual[1000] = []
    cards[1005].stock_wb = 0
    rep = build_report(actual, cards, {}, PROF, frozenset())
    assert rep.no_recs == [1000]
    assert any(r == 1005 for _, r in rep.no_stock) and not rep.ok
    assert summary("skazka", rep, {})[0] == "warn"


def test_rule_violations_brand_eco_imt_self_duplicate():
    cards = {1: card(1, 10), 2: card(2, 11, prefix="MT"), 3: card(3, 5928), 4: card(4, 12), 5: card(5, 13)}
    actual = {1: [2, 3, 4, 4, 1], 2: [1], 3: [1], 4: [1], 5: [1]}
    rep = build_report(actual, cards, {1: 7, 4: 7}, PROF, frozenset({"5928"}))
    why = {w for _, _, w in rep.rules}
    assert {"бренд", "эко", "из своей склейки", "карточка рекомендует саму себя", "дубль в списке"} <= why


def test_short_lists_only_counted_not_failed():
    cards = world()
    actual = lists(cards, k=15)
    rep = build_report(actual, cards, {}, PROF, frozenset())
    assert rep.ok and len(rep.short) == 30


def test_no_report_when_not_filled_and_stale_report_removed(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(wr, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(wr, "notice", lambda level, title, text, key: sent.append((level, title)))
    stale = tmp_path / "report_milky_garden.xlsx"
    stale.write_text("прошлый запуск")
    monkeypatch.setattr(wr, "jam_sync", lambda *a, **k: (["milky_garden: Chrome на порту 9223: нет входа в кабинет WB"], {}, {}))
    problems = wr.finish_jam("milky_garden", PROF, None, [], {}, None, tmp_path, {}, frozenset())
    assert problems and not stale.exists() and sent[0][0] == "error" and "не заполнены" in sent[0][1]


def test_report_written_only_after_success(tmp_path, monkeypatch):
    sent = []
    cards = list(world(25).values())
    actual = lists({c.nm_id: c for c in cards})
    monkeypatch.setattr(wr, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(wr, "notice", lambda level, title, text, key: sent.append((level, title, text)))
    monkeypatch.setattr(wr, "jam_sync", lambda *a, **k: ([], actual, {"changed": 3, "matched": 3}))
    assert wr.finish_jam("milky_garden", {**PROF}, None, cards, {}, None, tmp_path, {}, frozenset()) == []
    assert (tmp_path / "report_milky_garden.xlsx").exists() and sent[0][0] == "ok" and "Подробности" in sent[0][2]
