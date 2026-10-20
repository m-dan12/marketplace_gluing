"""Строки БД -> карточки планировщика WB: бренд по префиксу, остаток, отрезы из рулонов, сверка с брендом WB."""
from __future__ import annotations

from dataclasses import dataclass, field

from gluing.brands import brand_for
from gluing.cards import _FABRIC_KEY
from gluing.lists import child_designs, eco_designs
from gluing.model import Card
from gluing.parsing import parse_article
from gluing.rules import Context


@dataclass
class Loaded:
    cards: list[Card]
    live: dict[int, int]                              # nm_id -> imtID
    skipped: dict[str, int] = field(default_factory=dict)
    unknown_prefix: list[str] = field(default_factory=list)
    brand_mismatch: list[tuple[str, str, str]] = field(default_factory=list)   # (артикул, бренд по префиксу, бренд на WB)
    no_live: list[str] = field(default_factory=list)  # активные карточки без живого imtID


def context_for(profile: dict) -> Context:
    """Списки эко и детских дизайнов ведутся только для Профтекса (use_lists = false в профиле отключает их)."""
    use = profile["rules"].get("use_lists", True)
    return Context(profile, eco_designs() if use else frozenset(), child_designs() if use else frozenset())


def load_cards(rows: list[dict], rolls: dict[str, int], profile: dict) -> Loaded:
    alias = profile.get("brands", {}).get("wb_alias", {})
    fabric = profile["rules"]["fabric_category"]
    cards: list[Card] = []
    live: dict[int, int] = {}
    skipped: dict[str, int] = {}
    out = Loaded(cards, live, skipped)

    def skip(why: str):
        skipped[why] = skipped.get(why, 0) + 1

    for r in rows:
        art = (r["offer_id"] or "").strip()
        info = parse_article(art)
        brand = brand_for(info["prefix"])
        if brand is None:
            skip("неизвестный префикс")
            out.unknown_prefix.append(art)
            continue
        if not info["key"]:
            skip("нет ключа в артикуле")
            continue
        c = Card(article=art, nm_id=int(r["nm_id"]), title=r.get("title") or "", category=r.get("category") or "", brand=brand,
                 prefix=info["prefix"], design=info["design"], key=info["key"], key_nums=info["key_nums"],
                 stock_wb=int(r.get("stock_wb") or 0), stock_fbs=int(r.get("stock_fbs") or 0), orders30=int(r.get("orders30") or 0))
        if c.category == fabric:
            m = _FABRIC_KEY.match(c.key)
            code = f"{c.prefix}{c.design}"
            if m and code in rolls:
                c.roll_code, c.roll_len_m = code, int(m.group(1))
                c.stock_roll = rolls[code] // c.roll_len_m
        if c.stock_total <= 0:
            skip("нет остатка")
            continue
        wb_brand = alias.get(r.get("wb_brand") or "", r.get("wb_brand") or "")
        if wb_brand and wb_brand != brand:
            out.brand_mismatch.append((art, brand, r.get("wb_brand") or ""))
        if r.get("imt_id") is not None:
            live[c.nm_id] = int(r["imt_id"])
        else:
            out.no_live.append(art)
        cards.append(c)
    return out
