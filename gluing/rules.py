"""Правила: каждое решает, в какую «корзину» попадает карточка, или не срабатывает (None).

Корзина = (бренд, категория, id корзины); внутри корзины карточки делятся поровну, если их больше лимита.
Порядок правил задаёт профиль; первое сработавшее правило определяет корзину.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .model import Card
from .parsing import design_candidates, is_kant


@dataclass(frozen=True)
class Bucket:
    brand: str
    category: str
    bid: str         # идентификатор корзины внутри бренда и категории
    rule: str        # название правила для листа «Склейки»
    why: str = field(default="", compare=False)   # объяснение для карточки, в ключ корзины не входит
    mergeable: bool = False   # можно склеивать мелкие корзины между собой (только generic)


class Context:
    def __init__(self, profile: dict, eco: frozenset[str], child: frozenset[str]):
        r = profile["rules"]
        self.p = r
        self.eco, self.child = eco, child
        self.bedding = set(r["bedding_categories"])
        self.ruffle_ends = set(r["ruffle_ends"])

    def in_list(self, c: Card, lst: frozenset[str]) -> bool:
        return any(d in lst for d in design_candidates(c.design))


Rule = Callable[[Card, Context], "Bucket | None"]


def carnival(c: Card, x: Context):
    if c.category != x.p["carnival_category"]:
        return None
    title = c.title.lower()
    for stem, name in x.p["carnival_types"].items():
        if stem in title:
            return Bucket(c.brand, c.category, f"carnival:{name}", f"Карнавал — {name}", f"в названии «{stem}»")
    return None


def decor(c: Card, x: Context):
    if c.category == x.p["decor_pillowcase_category"]:
        return Bucket(c.brand, c.category, "decor", "Декоративные наволочки", "категория декоративных наволочек")


def whole_category(c: Card, x: Context):
    word = x.p["whole_category_brands"].get(c.brand)
    if word and c.category in x.bedding:
        return Bucket(c.brand, c.category, "whole", f"{c.brand} — {word}", f"бренд {c.brand}: вся категория вместе")


def button(c: Card, x: Context):
    if c.category == "Наволочки" and c.key == x.p["button_pillowcase_key"]:
        return Bucket(c.brand, c.category, "button", f"Наволочки {c.key}",
                      "наволочки с пуговицами вместе, независимо от дизайна")


def kant(c: Card, x: Context):
    if is_kant(c.article):
        return Bucket(c.brand, c.category, "kant", "KANT", "в артикуле KANT: по категории независимо от ключа")


def _is_ruffle(c: Card, x: Context) -> bool:
    return (len(c.key_nums) >= 3 and c.key_nums[2] in x.ruffle_ends
            and c.category in {"Наволочки", "Постельное белье"} and c.brand in x.p["ruffle_brands"])


def _eco_ok(c: Card, x: Context) -> bool:
    return c.category in x.bedding and c.brand not in x.p["no_eco_brands"] and x.in_list(c, x.eco)


def eco_ruffle(c: Card, x: Context):
    if _eco_ok(c, x) and _is_ruffle(c, x):
        kind = "комплекты" if c.category == "Постельное белье" else "наволочки"
        return Bucket(c.brand, c.category, "eco_ruffle", f"ЭКО — рюши — {kind}", "эко-дизайн и ключ с рюшами")


def eco(c: Card, x: Context):
    if _eco_ok(c, x):
        return Bucket(c.brand, c.category, "eco", "ЭКО", f"дизайн {c.design} в списке эко")


def child(c: Card, x: Context):
    if c.category in x.bedding and x.in_list(c, x.child):
        return Bucket(c.brand, c.category, "child", "Детские", f"дизайн {c.design} в списке детских")


def ruffle(c: Card, x: Context):
    if _is_ruffle(c, x):
        kind = "комплекты" if c.category == "Постельное белье" else "наволочки"
        return Bucket(c.brand, c.category, "ruffle", f"Рюши — {kind}", "ключ с рюшами: отдельная склейка")


def rhomb(c: Card, x: Context):
    if c.category == "Пододеяльники" and c.key_nums and c.key_nums[0] in x.p["rhomb_first"]:
        return Bucket(c.brand, c.category, f"rhomb:{c.key}", f"РОМБЫ Пододеяльники — только {c.key}",
                      "ромбы: только свой ключ")


def generic(c: Card, x: Context):
    key = c.key
    if key == x.p["hotel_pillowcase_key"] and c.category == "Наволочки":
        key = x.p["hotel_pillowcase_to"]          # отель 0-0-260 приклеиваем к 0-0-26
    return Bucket(c.brand, c.category, f"key:{key}", f"Ключ {key}", f"ключ {key}", mergeable=True)


REGISTRY: dict[str, Rule] = {
    "carnival": carnival, "decor": decor, "whole_category": whole_category, "button": button,
    "kant": kant, "eco_ruffle": eco_ruffle, "eco": eco, "child": child, "ruffle": ruffle,
    "rhomb": rhomb, "generic": generic,
}


def assign(c: Card, x: Context, order: list[str]) -> Bucket:
    for name in order:
        b = REGISTRY[name](c, x)
        if b is not None:
            return b
    raise RuntimeError(f"{c.article}: ни одно правило не сработало")
