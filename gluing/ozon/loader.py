"""Загрузка карточек Озона из CSV-снимка и сборка дизайнов (с отрезами из рулонов для тканей)."""
from __future__ import annotations

import csv
import re
from pathlib import Path

from ..parsing import parse_article
from .features import pick_feature, sku_feature
from .model import Design, OzonSku

FABRIC_TYPE = "Ткань"
_CUT = re.compile(r"^1-(\d+)00220$")          # ключ ткани: 1-500220 = отрез 5 м × 2,2 м


def _type_of(category: str) -> str:
    return category.split(" > ")[-1] if category else "?"


def load_rolls(path: Path | str) -> dict[str, int]:
    """CSV article, meters: рулоны на складе FBS Селсапа (артикул PTxxxx, метры)."""
    with open(path, encoding="utf-8", newline="") as f:
        return {r["article"]: int(float(r["meters"])) for r in csv.DictReader(f)}


def apply_roll(s: OzonSku, typ: str, rolls: dict[str, int]) -> None:
    """Для ткани добавляет отрезы из рулона: метры // длина отреза. Шторы и прочее рулонов не получают."""
    if typ != FABRIC_TYPE:
        return
    m = _CUT.match(s.key)
    code = s.prefix + s.design_no
    if m and code in rolls:
        s.roll_code, s.roll_m, s.cut_m = code, rolls[code], int(m.group(1))
        s.stock_roll = rolls[code] // s.cut_m


def build_designs(skus: list[OzonSku], accounts: list[str], profile: dict) -> tuple[list[Design], list[str]]:
    """Дизайны по кабинету, бренду (из префикса) и типу. Возвращает (дизайны, offer_id с неизвестным брендом).

    В склейки идут SKU с остатком больше 1 (Озон FBO+FBS плюс отрезы из рулона)."""
    brands = profile["brands"]
    designs: dict[tuple, Design] = {}
    unknown: list[str] = []
    for s, acc in zip(skus, accounts):
        if s.stock + s.stock_roll <= 1:
            continue
        brand = brands.get(s.prefix)
        if brand is None:
            unknown.append(s.offer_id)                    # префикс не из списка: не склеиваем молча
            continue
        typ = _type_of(s.category)
        key = (acc, brand, typ, s.prefix + s.design_no)
        d = designs.get(key)
        if d is None:
            d = designs[key] = Design(account=acc, brand=brand, typ=typ, design=s.prefix + s.design_no,
                                      digits=s.design_no)
        d.skus.append(s)
        d.features[sku_feature(typ, s)] += 1
    out = list(designs.values())
    for d in out:
        d.feature = pick_feature(d)
    return out, unknown


def load_designs(path: Path | str, profile: dict, rolls_path: Path | str | None = None):
    """CSV с колонками account, offer_id, title, category, stock, orders28. rolls_path — рулоны для тканей."""
    rolls = load_rolls(rolls_path) if rolls_path else {}
    skus: list[OzonSku] = []
    accounts: list[str] = []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            p = parse_article(r["offer_id"])
            if not p["design"]:
                continue                                  # артикул не по шаблону «префикс+цифры/ключ»
            s = OzonSku(offer_id=r["offer_id"], title=r["title"] or "", category=r["category"] or "",
                        stock=int(float(r["stock"])), orders28=int(float(r["orders28"] or 0)),
                        prefix=p["prefix"], design_no=p["design"], key=p["key"], key_nums=p["key_nums"])
            apply_roll(s, _type_of(s.category), rolls)
            skus.append(s)
            accounts.append(r["account"])
    return build_designs(skus, accounts, profile)
