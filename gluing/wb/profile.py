"""Профили WB «площадка + кабинет»: файл может расширять другой (`extends`), разделы переопределяются по ключам."""
from __future__ import annotations

import tomllib
from pathlib import Path

PROFILES = Path(__file__).resolve().parents[2] / "profiles"


def load_profile(name_or_path: str | Path) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = PROFILES / (str(name_or_path) if str(name_or_path).endswith(".toml") else f"{name_or_path}.toml")
    with open(p, "rb") as f:
        prof = tomllib.load(f)
    base = prof.pop("extends", None)
    if base:
        merged = load_profile(p.parent / base)
        for k, v in prof.items():
            merged[k] = {**merged.get(k, {}), **v} if isinstance(v, dict) and isinstance(merged.get(k), dict) else v
        prof = merged
    return prof
