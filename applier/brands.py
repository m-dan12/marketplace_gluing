"""Бренд на Озоне (атрибут 85): соответствие «бренд плана → значение на Озоне» и выбор id справочника.

Истина — префикс артикула (бренд плана). На Озоне бренд — значение справочника, и одно и то же слово («Сказка») встречается
в справочнике под несколькими id, поэтому id берётся не поиском по тексту, а как самый частый в живых карточках кабинета
той же категории. Чужие бренды (не из таблицы профиля) не перезаписываются."""
from __future__ import annotations

from collections import Counter

BRAND_ATTR = 85


def load_brand_map(profile: dict) -> dict[str, str]:
    """[ozon_brand] профиля: {бренд плана: значение бренда на Озоне}. Пусто — запись бренда выключена."""
    return dict(profile.get("ozon_brand") or {})


def _text(info: dict) -> str:
    v = (info.get("attrs") or {}).get(str(BRAND_ATTR)) or []
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
