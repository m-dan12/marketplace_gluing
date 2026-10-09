"""Сборка единой таблицы карточек WB: остатки WB РФ + FBS + отрезы из рулонов, заказы, бренд."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from .brands import brand_for
from .model import Card
from .parsing import parse_article

_ROLL_ARTICLE = re.compile(r"^PT\d+$")
_FABRIC_KEY = re.compile(r"^1-(\d+)00220$")      # 1-500220 -> отрез 5 м


def _read(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def load_rolls(sel_fbs: list[dict]) -> dict[str, int]:
    """Рулоны: склад FBS, артикул вида PTxxxx, остаток в метрах."""
    rolls: dict[str, int] = defaultdict(int)
    for r in sel_fbs:
        art = (r["article"] or "").strip()
        if _ROLL_ARTICLE.match(art):
            rolls[art] += _int(r["quantity"])
    return dict(rolls)


def build_wb_cards(fixtures: Path, profile: dict) -> tuple[list[Card], dict]:
    wb_stock = _read(fixtures / "g_wb_stock.csv")
    sel_fbs = _read(fixtures / "g_sel_fbs.csv")
    cards_ref = _read(fixtures / "g_cards.csv")
    orders = _read(fixtures / "g_orders.csv")

    ref = {r["article"]: r for r in cards_ref}
    wb_rf = {r["vendor_code"]: r for r in wb_stock}
    fbs: dict[str, int] = defaultdict(int)
    for r in sel_fbs:
        fbs[(r["article"] or "").strip()] += _int(r["quantity"])
    ord_by_art = {r["supplier_article"]: r for r in orders}
    rolls = load_rolls(sel_fbs)

    fabric_cat = profile["rules"]["fabric_category"]
    cards: list[Card] = []
    unknown: list[str] = []
    for art, r in ref.items():
        info = parse_article(art)
        c = Card(article=art, title=r.get("title") or "", category=r.get("category") or "",
                 prefix=info["prefix"], design=info["design"], key=info["key"], key_nums=info["key_nums"])
        c.nm_id = _int(r.get("external_id")) or None
        c.stock_wb = _int(wb_rf.get(art, {}).get("wb_rf"))
        c.stock_fbs = fbs.get(art, 0)
        if art in wb_rf:
            c.barcode = (wb_rf[art].get("barcodes") or "").split(";")[0]
            c.nm_id = c.nm_id or _int(wb_rf[art].get("nm_id"))
        if c.category == fabric_cat:
            m = _FABRIC_KEY.match(c.key)
            code = f"{c.prefix}{c.design}"
            if m and code in rolls:
                c.roll_code, c.roll_len_m = code, int(m.group(1))
                c.stock_roll = rolls[code] // c.roll_len_m
        o = ord_by_art.get(art)
        if o:
            c.orders30, c.rub30 = _int(o["orders30"]), _int(o["rub30"])
        if c.stock_total <= 0:
            continue
        brand = brand_for(c.prefix)
        if brand is None:
            c.flags.add("brand_unknown")
            unknown.append(art)
        c.brand = brand or ""
        cards.append(c)
    return cards, {"rolls": rolls, "unknown_brand": unknown}
