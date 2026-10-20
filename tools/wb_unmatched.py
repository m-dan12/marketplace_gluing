"""Активные карточки WB, которые планировщик не может склеить: неизвестный префикс и нет ключа в артикуле (только чтение БД).

    python -m tools.wb_unmatched [--account skazka]"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date

import psycopg

from gluing.brands import brand_for
from gluing.parsing import parse_article
from gluing.wb.profile import load_profile
from plan_ozon import database_url
from plan_wb import CABINETS
from planner.reader import read_rolls
from planner.wb_reader import read_wb_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    a = ap.parse_args()
    with psycopg.connect(database_url()) as conn:
        for acc in [a.account] if a.account else CABINETS:
            prof = load_profile(CABINETS[acc])
            rows = read_wb_rows(conn, acc, date.today().isoformat(), prof["stock"]["wb_mode"])
            unk, nokey = [], []
            for r in rows:
                if (r["stock_wb"] or 0) + (r["stock_fbs"] or 0) <= 0 and r["category"] != prof["rules"]["fabric_category"]:
                    continue
                art = (r["offer_id"] or "").strip()
                p = parse_article(art)
                if brand_for(p["prefix"]) is None:
                    unk.append(r)
                elif not p["key"]:
                    nokey.append(r)
            print(f"\n=== {acc}: неизвестный префикс {len(unk)}, нет ключа {len(nokey)}")
            for title, lst in (("неизвестный префикс", unk), ("нет ключа", nokey)):
                print(f"-- {title}: по категориям {dict(Counter(r['category'] for r in lst).most_common(6))}; "
                      f"бренды WB {dict(Counter(r['wb_brand'] for r in lst).most_common(5))}")
                print("   префиксы:", dict(Counter(parse_article(r['offer_id'])['prefix'] or '(нет)' for r in lst).most_common(8)))
                for r in lst[:12]:
                    print(f"   {r['offer_id']!r:40} {r['category']} | {r['wb_brand']} | остаток {r['stock_wb']}+{r['stock_fbs']}")
        conn.rollback()


if __name__ == "__main__":
    main()
