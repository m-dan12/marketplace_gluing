"""Сборка склеек Озона.

Один день:   python build_ozon.py profiles/ozon.toml --cur fixtures/g_ozon.csv --out output/ozon.xlsx
С состоянием: добавьте --prev-state output/state/ozon_prev.json (вчерашние решения и группы) и --state-out ...
Проверка стабильности: --prev-csv fixtures/g_ozon_prev.csv (сначала считает вчера без состояния, потом сегодня).
"""
from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path

from gluing.ozon.loader import load_designs
from gluing.ozon.metrics import churn, quality
from gluing.ozon.report import write_report
from gluing.ozon.tree import build


def run(profile: dict, csv_path: str, prev_state: dict | None = None, rolls: str | None = None):
    designs, unknown = load_designs(csv_path, profile, rolls)
    groups, log, state = build(designs, profile, prev_state)
    return designs, unknown, groups, log, state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--cur", required=True)
    ap.add_argument("--prev-csv", default="")
    ap.add_argument("--rolls", default="", help="CSV рулонов (article, meters) для тканей")
    ap.add_argument("--prev-rolls", default="")
    ap.add_argument("--prev-state", default="")
    ap.add_argument("--state-out", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    with open(a.profile, "rb") as f:
        profile = tomllib.load(f)

    prev_state = None
    if a.prev_csv:
        _, _, _, _, prev_state = run(profile, a.prev_csv, None, a.prev_rolls or None)   # вчера: без состояния
    elif a.prev_state:
        prev_state = json.loads(Path(a.prev_state).read_text(encoding="utf-8"))

    designs, unknown, groups, log, state = run(profile, a.cur, prev_state, a.rolls or None)
    q = quality(groups)
    ch = churn(prev_state, state) if prev_state else None
    for k, v in q.items():
        print(f"{k}: {v}")
    if ch:
        print("--- стабильность к предыдущему запуску")
        for k, v in ch.items():
            print(f"{k}: {v}")
    print(f"артикулов с неизвестным брендом: {len(unknown)}")
    if a.state_out:
        Path(a.state_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.state_out).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    if a.out:
        write_report(a.out, groups, designs, log, q, ch, unknown)
        print("файл:", a.out)


if __name__ == "__main__":
    main()
