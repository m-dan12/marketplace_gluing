"""Запуск сборки склеек: python build.py profiles/wb_proftex.toml [--fixtures fixtures]"""
from __future__ import annotations

import argparse
import tomllib
from datetime import date
from collections import Counter
from pathlib import Path

from gluing.builder import build
from gluing.cards import build_wb_cards
from gluing.checks import hard_violations, quality, soft_flags
from gluing.lists import child_designs, eco_designs
from gluing.report import write_report
from gluing.rules import Context


def run(profile_path: str, fixtures: str, solver: str = "greedy", weights: dict | None = None, time_limit: float = 0):
    with open(profile_path, "rb") as f:
        profile = tomllib.load(f)
    if time_limit:
        profile.setdefault("solver", {})["deterministic_limit"] = time_limit
    if weights:
        profile.setdefault("solver", {}).setdefault("weights", {}).update(weights)
    cards, info = build_wb_cards(Path(fixtures), profile)
    groups = build(cards, profile, Context(profile, eco_designs(), child_designs()), solver)
    return profile, cards, info, groups


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("--fixtures", default="fixtures")
    ap.add_argument("--out", default="")
    ap.add_argument("--solver", choices=["greedy", "cpsat"], default="greedy")
    ap.add_argument("--time-limit", type=float, default=0)
    ap.add_argument("--weight", action="append", default=[], help="вес цели, например span=80")
    args = ap.parse_args()
    profile, cards, info, groups = run(args.profile, args.fixtures, args.solver, {k: int(v) for k, v in (w.split("=") for w in args.weight)}, args.time_limit)
    bad = hard_violations(groups, profile)
    flags = soft_flags(groups, profile)
    sizes = [len(g.cards) for g in groups]
    print(f"карточек с остатком: {len(cards)}, склеек: {len(groups)}, размер {min(sizes)}..{max(sizes)}")
    print(f"жёсткие нарушения: {len(bad)}", *bad[:5], sep="\n  ")
    print({k: len(v) for k, v in flags.items()})
    print(quality(groups, profile))
    if args.out:
        write_report(Path(args.out), groups, cards, info, flags, bad, profile, date.today())
        print("файл:", args.out)


if __name__ == "__main__":
    main()
