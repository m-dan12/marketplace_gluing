"""Загрузка атрибутов карточек Озона (ТОЛЬКО ЧТЕНИЕ с площадки) в gluing.sku_attr и gluing.category_attr.

Методы Озона: /v4/product/info/attributes (атрибуты товаров), /v1/description-category/attribute (атрибуты категории,
флаг is_aspect). Ключи кабинетов берутся из .env проекта db_sync (в память, нигде не печатаются).

    python -m planner.ozon_attrs --env C:\\Proftex-Server\\marketplace_db_sync\\.env [--limit 20] [--dry]
"""
from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import httpx

BASE = "https://api-seller.ozon.ru"
ACCOUNT_ENV = {"skazka": ("OZON_CLIENT_ID", "OZON_API_KEY"),
               "timeless": ("OZON_CLIENT_ID_TIMELESS", "OZON_API_KEY_TIMELESS"),
               "milky_garden": ("OZON_CLIENT_ID_MILKY", "OZON_API_KEY_MILKY")}
BATCH = 1000


def read_env(path: str | Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=([^#\r\n]*)", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def post(client: httpx.Client, headers: dict, path: str, body: dict, tries: int = 5) -> dict:
    for i in range(tries):
        r = client.post(BASE + path, headers=headers, json=body)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (i + 1))
            continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def fetch_attributes(client, headers, offer_ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(offer_ids), BATCH):
        chunk = offer_ids[i:i + BATCH]
        last = ""
        while True:
            d = post(client, headers, "/v4/product/info/attributes",
                     {"filter": {"offer_id": chunk, "visibility": "ALL"}, "limit": BATCH, "last_id": last, "sort_dir": "ASC"})
            out.extend(d.get("result") or [])
            last = d.get("last_id") or ""
            if not last or len(d.get("result") or []) < BATCH:
                break
    return out


def fetch_category_attrs(client, headers, category_id: int, type_id: int) -> list[dict]:
    d = post(client, headers, "/v1/description-category/attribute",
             {"description_category_id": category_id, "type_id": type_id, "language": "DEFAULT"})
    return d.get("result") or []


def flatten(item: dict) -> dict[str, list[str]]:
    attrs: dict[str, list[str]] = {}
    for a in item.get("attributes") or []:
        vals = [str(v.get("value", "")).strip() for v in a.get("values") or []]
        attrs[str(a["id"])] = vals
    return attrs


def run(conn, env_path: str, limit: int | None, dry: bool) -> dict:
    env = read_env(env_path)
    with conn.cursor() as cur:
        cur.execute("select account, article from product_cards where marketplace='ozon' order by 1, 2")
        rows = cur.fetchall()
    by_acc: dict[str, list[str]] = {}
    for acc, offer in rows:
        by_acc.setdefault(acc, []).append(offer)
    stats = {"accounts": {}, "categories": 0}
    cats: dict[tuple[int, int], list[dict]] = {}
    with httpx.Client(timeout=120.0) as client:
        for acc, offers in by_acc.items():
            if acc not in ACCOUNT_ENV or not all(k in env for k in ACCOUNT_ENV[acc]):
                stats["accounts"][acc] = "нет ключей, пропущен"
                continue
            cid, key = ACCOUNT_ENV[acc]
            headers = {"Client-Id": env[cid], "Api-Key": env[key]}
            offers = offers[:limit] if limit else offers
            items = fetch_attributes(client, headers, offers)
            stats["accounts"][acc] = len(items)
            for it in items:
                k = (it.get("description_category_id"), it.get("type_id"))
                if None not in k and k not in cats:
                    cats[k] = fetch_category_attrs(client, headers, *k)
            if dry:
                continue
            with conn.transaction(), conn.cursor() as cur:
                cur.executemany(
                    "insert into gluing.sku_attr(account,offer_id,product_id,category_id,type_id,name,attrs,fetched_at)"
                    " values (%s,%s,%s,%s,%s,%s,%s, now()) on conflict (account,offer_id) do update set"
                    " product_id=excluded.product_id, category_id=excluded.category_id, type_id=excluded.type_id,"
                    " name=excluded.name, attrs=excluded.attrs, fetched_at=now()",
                    [(acc, it["offer_id"], it.get("id"), it.get("description_category_id"), it.get("type_id"),
                      it.get("name"), json.dumps(flatten(it), ensure_ascii=False)) for it in items])
    stats["categories"] = len(cats)
    if not dry:
        with conn.transaction(), conn.cursor() as cur:
            for (c, t), attrs in cats.items():
                cur.executemany(
                    "insert into gluing.category_attr(category_id,type_id,attr_id,name,is_aspect,is_required)"
                    " values (%s,%s,%s,%s,%s,%s) on conflict (category_id,type_id,attr_id) do update set"
                    " name=excluded.name, is_aspect=excluded.is_aspect, is_required=excluded.is_required",
                    [(c, t, a["id"], a.get("name", ""), bool(a.get("is_aspect")), bool(a.get("is_required"))) for a in attrs])
    return stats


def main():
    import psycopg
    from plan_ozon import database_url
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, help=".env проекта db_sync с ключами Озона")
    ap.add_argument("--limit", type=int, default=0, help="сколько артикулов на кабинет (для пробы)")
    ap.add_argument("--dry", action="store_true", help="не писать в БД")
    a = ap.parse_args()
    with psycopg.connect(database_url()) as conn:
        print(run(conn, a.env, a.limit or None, a.dry))


if __name__ == "__main__":
    main()
