"""Рекомендации продавца WB: считает по кабинетам и пишет файлы для загрузки в Джем (только чтение БД, на WB ничего не уходит).

    python -m tools.wb_recommend                        # все кабинеты, файлы в out/recommend/<дата>/
    python -m tools.wb_recommend --accounts skazka --out out/rec
Загрузка: Джем -> подборка «Рекомендации продавца» -> добавить -> через шаблон Excel. Файл без шапки: A — артикул WB, к которому рекомендуем
(20 одинаковых строк), B — рекомендуемый артикул WB; до 10000 строк на файл. Рядом лежит <кабинет>_details.csv: слот, артикулы продавца, названия.

Запись через API (токен записи склеек из MARKETPLACE_MERGE_ENV): --apply; --limit N — только первые N карточек (проба); перед записью текущие
рекомендации сохраняются в <кабинет>_backup.json, вернуть их: --restore <файл> --accounts <кабинет>. Без --apply на WB ничего не пишется.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date
from pathlib import Path

import openpyxl
import psycopg

from gluing.brands import brand_for
from gluing.cards import _FABRIC_KEY
from gluing.lists import eco_designs
from gluing.model import Card
from gluing.parsing import parse_article
from gluing.wb.profile import load_profile
from plan_ozon import database_url
from plan_wb import CABINETS
from planner.reader import read_rolls
from applier.ozon_client import read_env
from applier.wb_client import api_for, conf, merge_env_path
from planner.wb_reader import read_imt_members, read_wb_rows
from recommend.wb import recommend
from recommend.wb_send import backup, load_backup, read_current, send


def load_cards(rows: list[dict], rolls: dict[str, int], prof: dict) -> list[Card]:
    """Карточки с остатком и известным брендом; ключ размера не обязателен (у декора и карнавала его нет)."""
    cards = []
    for r in rows:
        art = (r["offer_id"] or "").strip()
        info = parse_article(art)
        brand = brand_for(info["prefix"])
        if brand is None:
            continue
        c = Card(article=art, nm_id=int(r["nm_id"]), title=r.get("title") or "", category=r.get("category") or "", brand=brand,
                 prefix=info["prefix"], design=info["design"], key=info["key"], key_nums=info["key_nums"],
                 stock_wb=int(r.get("stock_wb") or 0), stock_fbs=int(r.get("stock_fbs") or 0), orders30=int(r.get("orders30") or 0))
        if c.category == prof["rules"]["fabric_category"]:
            m = _FABRIC_KEY.match(c.key)
            code = f"{c.prefix}{c.design}"
            if m and code in rolls:
                c.roll_code, c.roll_len_m = code, int(m.group(1))
                c.stock_roll = rolls[code] // c.roll_len_m
        if c.stock_total > 0 and c.design:
            cards.append(c)
    return cards


def write_files(acc: str, cards: dict[int, Card], res, out: Path, rows_per_file: int, total: int) -> list[Path]:
    per_file = max(rows_per_file // total, 1)                    # карточка целиком в одном файле
    targets = sorted(res.recs)
    paths = []
    for i in range(0, len(targets), per_file):
        wb = openpyxl.Workbook(write_only=True)
        ws = wb.create_sheet()
        for t in targets[i:i + per_file]:
            for nm, _ in res.recs[t]:
                ws.append([t, nm])
        p = out / f"{acc}_{i // per_file + 1}.xlsx"
        wb.save(p)
        paths.append(p)
    with open(out / f"{acc}_details.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["nm_id", "артикул", "категория", "рекомендуем nm_id", "артикул", "категория", "слот", "название"])
        for t in targets:
            for nm, slot in res.recs[t]:
                a, b = cards[t], cards[nm]
                w.writerow([t, a.article, a.category, nm, b.article, b.category, slot, b.title])
    return paths


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--accounts", nargs="*", default=list(CABINETS), choices=list(CABINETS))
    ap.add_argument("--out", default="")
    ap.add_argument("--apply", action="store_true", help="записать рекомендации на WB через API")
    ap.add_argument("--limit", type=int, default=0, help="с --apply: только первые N карточек")
    ap.add_argument("--merge", action="store_true", help="с --apply: добавить к старым рекомендациям, а не заменить")
    ap.add_argument("--restore", default="", help="файл backup.json: вернуть сохранённые рекомендации (один кабинет)")
    a = ap.parse_args()
    out = Path(a.out or Path("out") / "recommend" / a.as_of)
    out.mkdir(parents=True, exist_ok=True)
    env = read_env(conf("MARKETPLACE_ENV"))
    mp = merge_env_path()
    menv = read_env(mp) if mp else {}
    if a.restore:
        if len(a.accounts) != 1:
            raise SystemExit("--restore: укажите один кабинет в --accounts")
        old = load_backup(Path(a.restore))
        errs = send(api_for(a.accounts[0], env, menv), old, replace=True)
        print(f"восстановлено карточек {len(old)}, ошибок WB {len(errs)}")
        return
    with psycopg.connect(database_url()) as conn:
        rolls = read_rolls(conn)
        for acc in a.accounts:
            prof = load_profile(CABINETS[acc])
            rows = read_wb_rows(conn, acc, a.as_of, prof.get("stock", {}).get("wb_mode", "all_physical"))
            cards = load_cards(rows, rolls, prof)
            imt = {nm: i for i, nms in read_imt_members(conn, acc).items() for nm in nms}
            use = prof["rules"].get("use_lists", True)
            res = recommend(cards, imt, prof, eco_designs() if use else frozenset())
            total = prof["recommend"]["total"]
            files = write_files(acc, {c.nm_id: c for c in cards}, res, out, prof["recommend"]["rows_per_file"], total)
            full = sum(len(v) == total for v in res.recs.values())
            slots = Counter(s for v in res.recs.values() for _, s in v)
            print(f"{acc}: карточек с остатком {len(cards)}, с рекомендациями {len(res.recs)}, полных {full}, файлов {len(files)}; "
                  f"слоты {dict(slots)}; не набрано {dict(res.short)}; чаще всего рекомендуем {res.used.most_common(3)}")
            if a.apply:
                api = api_for(acc, env, menv)
                new = {t: [nm for nm, _ in v] for t, v in sorted(res.recs.items())}
                if a.limit:
                    new = dict(list(new.items())[:a.limit])
                cur = read_current(api, set(new))
                backup(out / f"{acc}_backup.json", cur)
                errs = send(api, new, replace=not a.merge)
                print(f"{acc}: записано карточек {len(new)} (до этого с рекомендациями {sum(1 for v in cur.values() if v)}), "
                      f"ошибок WB {len(errs)}: {errs[:3]}")


if __name__ == "__main__":
    main()
