"""Читает бренд (атрибут 85) и «Название модели» (9048) карточек Озона по списку артикулов. Только чтение.

    python tools/ozon_brands_read.py --brand-selsup "Cказка" --out ozon_brands_before.csv

Список артикулов берётся из последнего снимка selsup_stocks (все артикулы SelSup с этим написанием бренда),
ключи Озона — из .env сервера db_sync (в вывод не попадают).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SYNC_ENV = Path(os.environ.get("SYNC_ENV", r"C:\Proftex-Server\marketplace_db_sync\.env"))
CREDS = {"skazka": ("OZON_CLIENT_ID", "OZON_API_KEY"),
         "timeless": ("OZON_CLIENT_ID_TIMELESS", "OZON_API_KEY_TIMELESS"),
         "milky_garden": ("OZON_CLIENT_ID_MILKY", "OZON_API_KEY_MILKY")}
URL = "https://api-seller.ozon.ru/v4/product/info/attributes"
BRAND, MODEL = 85, 9048


def env() -> dict[str, str]:
    out = {}
    for line in SYNC_ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def fetch(client_id: str, api_key: str, offers: list[str]) -> dict[str, dict]:
    res: dict[str, dict] = {}
    for i in range(0, len(offers), 100):
        body = json.dumps({"filter": {"offer_id": offers[i:i + 100], "visibility": "ALL"}, "limit": 100}).encode()
        req = urllib.request.Request(URL, data=body, headers={
            "Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        for it in data.get("result", []):
            attrs = {a["id"]: ", ".join(v.get("value", "") for v in a.get("values", [])) for a in it.get("attributes", [])}
            ids = {a["id"]: ",".join(str(v.get("dictionary_value_id", "")) for v in a.get("values", []))
                   for a in it.get("attributes", [])}
            res[it["offer_id"]] = {"brand": attrs.get(BRAND, ""), "brand_id": ids.get(BRAND, ""),
                                   "model": attrs.get(MODEL, ""), "product_id": it.get("id")}
        time.sleep(0.3)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand-selsup", required=True, help="точное написание бренда в SelSup")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="взять не больше N артикулов (выборка)")
    a = ap.parse_args()
    import psycopg
    from plan_ozon import database_url

    with psycopg.connect(database_url()) as c, c.cursor() as cur:
        cur.execute("""
            select distinct p.account, s.article from selsup_stocks s
            join product_cards p on p.marketplace='ozon' and p.article = coalesce(nullif(s.ozon_article,''), s.article)
            where s.snapshot_date=(select max(snapshot_date) from selsup_stocks) and s.brand = %s
            order by 1, 2""", (a.brand_selsup,))
        pairs = cur.fetchall()
        if a.limit:
            pairs = pairs[:a.limit]
        c.rollback()
    e = env()
    rows = []
    for acc in sorted({p[0] for p in pairs}):
        cid, key = CREDS[acc]
        offers = [o for ac, o in pairs if ac == acc]
        got = fetch(e[cid], e[key], offers)
        for o in offers:
            g = got.get(o)
            rows.append({"account": acc, "offer_id": o, "found": bool(g),
                         "ozon_brand": g["brand"] if g else "", "ozon_brand_id": g["brand_id"] if g else "", "model_name": g["model"] if g else "",
                         "product_id": g["product_id"] if g else ""})
    with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("артикулов:", len(rows), "найдено на Озоне:", sum(r["found"] for r in rows))
    for (b, i), n in Counter((r["ozon_brand"] or "<пусто>", r["ozon_brand_id"]) for r in rows).most_common():
        print(f"  {b!r} id={i}: {n}")


if __name__ == "__main__":
    main()
