"""Размерность КПБ на Озоне: у комплектов постельного белья отдельная склейка на каждую размерность.

Размерность — по ключу артикула: первое число ключа, у двузначных (41, 42, 44, 51, 52, 54, 61, 62: с одеялом, кант, ромб)
первая цифра. Определения лежат в профиле (profiles/ozon.toml, [sizes]); код под отдельные размеры не нужен.
Бренды из mix_brands (например «Мечта», «Сказка Сатин») остаются целиком: все размеры дизайна в одной группе.
"""
from __future__ import annotations

import re

from .model import OzonSku


def lead_of(key: str) -> str:
    """Первое число ключа артикула: 4-13-19 -> 4, 52 -> 5, 44-13-0 -> 4."""
    first = key.split("-")[0] if key else ""
    return first[0] if len(first) == 2 and first.isdigit() else first


class Sizes:
    def __init__(self, cfg: dict):
        self.types = set(cfg.get("types", []))
        self.mix_brands = set(cfg.get("mix_brands", []))
        self.classes = {str(k): v for k, v in (cfg.get("lead") or {}).items()}

    @classmethod
    def from_profile(cls, profile: dict) -> "Sizes":
        return cls(profile.get("sizes", {}))

    def splits(self, typ: str, brand: str) -> bool:
        return typ in self.types and brand not in self.mix_brands

    def of(self, typ: str, brand: str, s: OzonSku) -> str:
        """Размерность SKU или пустая строка, если тип/бренд по размеру не делится."""
        if not self.splits(typ, brand):
            return ""
        lead = lead_of(s.key)
        if not lead or not re.fullmatch(r"\d+", lead):
            return "размер не определён"
        return self.classes.get(lead, f"размер {lead}")
