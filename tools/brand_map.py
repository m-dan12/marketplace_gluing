"""Сводка брендов SelSup к эталонным и сверка с префиксом артикула (только чтение).

    python tools/brand_map.py --out brand_map.csv

Эталон: «Сказка», «Сказка Сатин», «Мечта!»; «Анна Мария» пока не меняем (бренда на площадках нет); бренды других кабинетов (Milky Garden, Timeless) не трогаем.
Для каждого артикула SelSup: текущий бренд, бренд по префиксу, целевой бренд и статус
  ok        — уже эталонный и совпадает с префиксом
  fix       — приводится к эталонному по написанию, совпадает с префиксом
  by_prefix — написание не распознано («Не определен», «нет»), берём по префиксу
  conflict  — написание и префикс указывают на разные бренды: решает человек
  hold      — «Анна Мария»: не меняем, пока бренд не зарегистрирован на площадках
  keep      — бренд вне эталонного набора (другой кабинет/бренд), не меняем
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import tomllib
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LATIN = str.maketrans("CcAaEeOoPpXxMmTtHKBkp", "СсАаЕеОоРрХхМмТтНКВкр")   # латиница, похожая на кириллицу
# нормализованное написание (без пунктуации, регистра) -> эталон
BY_NAME = {"сказка": "Сказка", "сказкасатин": "Сказка Сатин", "мечта": "Мечта!", "аннамария": "Анна Мария", "annamaria": "Анна Мария"}
# префикс артикула -> эталон (по profiles/ozon.toml); «Сказка Сатин» — отдельный бренд
BY_PREFIX = {"PT": "Сказка", "РТ": "Сказка", "GR": "Сказка", "OK": "Сказка", "PC": "Сказка Сатин",
             "MT": "Мечта!", "AM": "Анна Мария", "PA": "Анна Мария"}

SQL = """
select account, article, ozon_article, brand from selsup_stocks
where snapshot_date = (select max(snapshot_date) from selsup_stocks)
group by 1, 2, 3, 4 order by 1, 2
"""


def norm(name: str | None) -> str:
    s = (name or "").translate(LATIN)
    return re.sub(r"[^а-яёa-z0-9]", "", s.casefold())


HOLD = {"Анна Мария"}      # бренда на площадках пока нет (там «нет бренда»): в SelSup не трогаем


def classify(brand: str | None, prefix: str) -> tuple[str, str]:
    t, st = _classify(brand, prefix)
    if t in HOLD:
        return brand or "", "hold"
    return t, st


def _classify(brand: str | None, prefix: str) -> tuple[str, str]:
    name_target = BY_NAME.get(norm(brand))
    pref_target = BY_PREFIX.get(prefix)
    if name_target is None:
        if norm(brand) in ("", "неопределен", "нет", "null"):
            return (pref_target, "by_prefix") if pref_target else (brand or "", "keep")
        return brand or "", "keep"
    if pref_target is None or pref_target == name_target:
        return name_target, ("ok" if brand == name_target else "fix")
    return name_target, "conflict"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="brand_map.csv")
    a = ap.parse_args()
    import psycopg
    from gluing.parsing import parse_article
    from plan_ozon import database_url

    rows = []
    with psycopg.connect(database_url()) as c, c.cursor() as cur:
        cur.execute(SQL)
        for account, article, ozon_article, brand in cur.fetchall():
            offer = ozon_article or article or ""
            prefix = parse_article(offer)["prefix"] if offer else ""
            target, status = classify(brand, prefix)
            rows.append({"account": account, "article": article, "ozon_article": ozon_article or "",
                         "prefix": prefix, "current_brand": brand or "", "target_brand": target, "status": status})
        c.rollback()
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("статусы:", dict(Counter(r["status"] for r in rows)))
    chg = Counter((r["current_brand"], r["target_brand"], r["status"]) for r in rows if r["status"] != "ok")
    for (cur_b, tgt, st), n in sorted(chg.items(), key=lambda x: -x[1]):
        print(f"{st:10} {cur_b!r:22} -> {tgt!r:16} {n}")


if __name__ == "__main__":
    main()
