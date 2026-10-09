from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).parent / "data"


def load_list(name: str) -> frozenset[str]:
    with open(DATA / name, encoding="utf-8", newline="") as f:
        return frozenset(r["design"].strip() for r in csv.DictReader(f) if r["design"].strip())


def eco_designs() -> frozenset[str]:
    return load_list("eco_designs.csv")


def child_designs() -> frozenset[str]:
    return load_list("child_designs.csv")
