"""Выгрузка снимка для расчёта на другой машине (только чтение БД): карточки, остатки, заказы, рулоны, подписи вариантов,
вчерашнее состояние плана.   python -m tools.export_snapshot --out <папка> [--as-of 2026-10-19]"""
from __future__ import annotations

import argparse
import csv
import json
import tomllib
from datetime import date
from pathlib import Path

import psycopg

from plan_ozon import database_url
from planner.reader import read_rolls, read_rows
from planner.store import PgStore
from planner.twins import load_aspects, load_attrs, signature


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--profile", default="profiles/ozon.toml")
    a = ap.parse_args()
    prof = tomllib.load(open(a.profile, "rb"))
    st = prof.get("stock", {})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(database_url()) as conn:
        rows = read_rows(conn, a.as_of, 28, st.get("grace_days", 0), st.get("min_total", 2))
        rolls = read_rolls(conn)
        attrs, aspects = load_attrs(conn), load_aspects(conn)
        prev = PgStore(conn).last("ozon")
        conn.rollback()
    cols = ["account", "offer_id", "title", "category", "stock", "orders28", "out_days", "product_id"]
    with open(out / "ozon_rows.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(out / "rolls.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["article", "meters"])
        w.writerows(rolls.items())
    sigs = {f"{acc}|{off}": [[aid, list(v)] for aid, v in signature(info, aspects)] for (acc, off), info in attrs.items()}
    (out / "sigs.json").write_text(json.dumps(sigs, ensure_ascii=False), encoding="utf-8")
    (out / "prev_state.json").write_text(json.dumps(prev.state if prev else {}, ensure_ascii=False), encoding="utf-8")
    meta = {"as_of": a.as_of, "rows": len(rows), "rolls": len(rolls), "sigs": len(sigs), "prev_run": prev.run.run_id if prev else None}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    print("выгружено:", meta)


if __name__ == "__main__":
    main()
