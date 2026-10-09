"""Бренд по префиксу артикула."""
from __future__ import annotations

BRAND_BY_PREFIX = {
    "PT": "Сказка", "GR": "Сказка", "OK": "Сказка",
    "PC": "Сказка Сатин",
    "MT": "Мечта",
    "AM": "Анна Мария", "PA": "Анна Мария",
    "MY": "Milky Garden", "MI": "Milky Garden", "KK": "Milky Garden",
    "": "Timeless",
}


def brand_for(prefix: str) -> str | None:
    """None — префикс неизвестен, такую карточку не склеиваем молча."""
    return BRAND_BY_PREFIX.get(prefix)
