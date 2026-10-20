"""Бренд на Озоне (атрибут 85): соответствие «бренд плана → значение на Озоне» и выбор id справочника.

Истина — префикс артикула (бренд плана). На Озоне бренд — значение справочника, и одно и то же слово («Сказка») встречается
в справочнике под несколькими id, поэтому id берётся не поиском по тексту, а как самый частый в живых карточках кабинета
той же категории. Чужие бренды (не из таблицы профиля) не перезаписываются."""
from __future__ import annotations

from collections import Counter

BRAND_ATTR = 85                    # «Бренд»
BRAND_ATTR_CLOTHES = 31           # «Бренд в одежде и обуви» (категория одежды, тот же справочник)
BRAND_ATTRS = (BRAND_ATTR, BRAND_ATTR_CLOTHES)
NO_BRAND = "нет бренда"           # Озон не отличает пустой бренд от «Нет бренда»: запись такого значения в пустое поле он пропускает (skipped)


class BrandMap(dict):
    """{бренд плана: бренд на Озоне}; prefixes — {префикс артикула: бренд плана} для групп из нескольких брендов («Мечта + Сказка Сатин»)."""
    prefixes: dict[str, str] = {}


def load_brand_map(profile: dict) -> BrandMap:
    """[ozon_brand] профиля: {бренд плана: значение бренда на Озоне}. Пусто — запись бренда выключена."""
    m = BrandMap(profile.get("ozon_brand") or {})
    m.prefixes = dict(profile.get("brands") or {})
    return m


def brand_attr_of(info: dict) -> int:
    """Характеристика бренда карточки по её категории (85 или 31)."""
    return info.get("brand_attr") or BRAND_ATTR


def _text(info: dict) -> str:
    v = (info.get("attrs") or {}).get(str(brand_attr_of(info))) or []
    return v[0] if v else ""


def brand_ids(live: dict) -> dict[tuple, int]:
    """Самый частый id для (кабинет, категория, значение) и запасной для (кабинет, None, значение); значение без регистра."""
    by_cat: dict[tuple, Counter] = {}
    for (acc, _), info in live.items():
        bid, txt = info.get("brand_id"), _text(info)
        if not bid or not txt:
            continue
        for cat in (info.get("cat"), None):
            by_cat.setdefault((acc, cat, txt.casefold()), Counter())[bid] += 1
    return {k: c.most_common(1)[0][0] for k, c in by_cat.items()}


def target_id(ids: dict[tuple, int], account: str, cat, text: str) -> int | None:
    k = text.casefold()
    return ids.get((account, cat, k)) or ids.get((account, None, k))


def allowed_current(brand_map: dict[str, str]) -> set[str]:
    """Значения бренда, которые можно заменять: пустое и наши (из таблицы профиля)."""
    return {""} | {v.casefold() for v in brand_map.values()}
