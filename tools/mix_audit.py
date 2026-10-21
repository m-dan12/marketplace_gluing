"""Аудит смешения в сохранённом плане Озона: размерности КПБ, эко и детские дизайны, разные «Названия цвета» у одного дизайна.

    python tools/mix_audit.py [--run <run_id>] [--type "Комплект постельного белья"]

Только чтение БД. Размерность — по ключу артикула (первое число: 4 = 1,5-сп, 5 = 2-сп, 6 = евро, 8 = семейный, 1/2 = детские)."""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from gluing.lists import child_designs, eco_designs  # noqa: E402
from gluing.parsing import design_candidates, parse_article  # noqa: E402
from plan_ozon import database_url  # noqa: E402
from planner.store import PgStore  # noqa: E402
from planner.twins import load_attrs  # noqa: E402


def size_of(offer_id: str) -> str:
    key = parse_article(offer_id)["key"]
    first = key.split("-")[0] if key else ""
    return first[0] if len(first) == 2 else first


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="")
    ap.add_argument("--type", default="Комплект постельного белья")
    a = ap.parse_args()
    eco, kids = eco_designs(), child_designs()
    import tomllib
    mix_brands = set(tomllib.load(open(ROOT / "profiles" / "ozon.toml", "rb")).get("sizes", {}).get("mix_brands", []))
    with psycopg.connect(database_url()) as conn:
        store = PgStore(conn)
        plan = store.load(a.run) if a.run else store.last("ozon")
        attrs = load_attrs(conn)
    print("план", plan.run.run_id)
    tot = defaultdict(Counter)
    for g in plan.groups:
        if g.type != a.type:
            continue
        sizes = Counter(size_of(m.offer_id) for d in g.designs for m in d.members)
        n_eco = sum(any(c in eco for c in design_candidates(d.design.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))) for d in g.designs)
        # «детское» у КПБ с размерностью — по размеру карточки (ключ 1/2), у брендов без деления по размеру — по списку дизайнов
        if any(b in mix_brands for b in g.brand.split(" + ")):
            n_kid = sum(any(c in kids for c in design_candidates(d.design.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))) for d in g.designs)
        else:
            n_kid = sum(any(size_of(m.offer_id) in ("1", "2") for m in d.members) for d in g.designs)
        t = tot[(g.account, g.brand)]
        t["групп"] += 1
        t["дизайнов"] += g.n_designs
        t["смешаны размеры"] += len(sizes) > 1
        t["эко вместе с обычными"] += 0 < n_eco < g.n_designs
        t["детские вместе с обычными"] += 0 < n_kid < g.n_designs
    for k, v in sorted(tot.items()):
        print(k, dict(v))
    # разные цвета у одного дизайна
    colors = defaultdict(set)
    for (acc, off), info in attrs.items():
        if info["cat"][1] == 92603:
            colors[(acc, off.split("/")[0])].add((info["attrs"].get("10097") or [""])[0])
    multi = sum(len(v) > 1 for v in colors.values())
    print(f"дизайнов КПБ с разными «Названиями цвета» у артикулов: {multi} из {len(colors)}")


if __name__ == "__main__":
    main()
