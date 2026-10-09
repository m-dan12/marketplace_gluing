"""Разбор артикула продавца: PT5948/0-0-20/1 -> префикс, дизайн, ключ."""
from __future__ import annotations

import re

_ART = re.compile(r"^([A-Za-zА-Яа-я]*?)(\d+)\s*/\s*(.*)$")
# русские буквы в артикулах — опечатка: РТ вместо PT и т.п.
_CYR2LAT = str.maketrans("РТАСМКОНЕВХ", "PTACMKOHEBX")


def parse_article(article: str) -> dict:
    """Возвращает prefix, design, key, key_nums. Ключ — часть между первым и вторым слешем без букв."""
    m = _ART.match(article.strip())
    if not m:
        return {"prefix": "", "design": "", "key": "", "key_nums": ()}
    prefix = m.group(1).upper().translate(_CYR2LAT)
    design = m.group(2)
    key_part = m.group(3).split("/")[0]
    key = re.sub(r"[^0-9-]", "", key_part).strip("-")
    key_nums = tuple(re.findall(r"\d+", key))
    return {"prefix": prefix, "design": design, "key": key, "key_nums": key_nums}


def design_candidates(design: str) -> list[str]:
    """Номера для поиска в списках: сам номер, а у 8-значных компаньонов первые 4 цифры."""
    out = [design]
    if len(design) == 8:
        out.append(design[:4])
    return out


def is_kant(article: str) -> bool:
    low = article.lower()
    return "kant" in low or "кант" in low
