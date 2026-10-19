"""Сравнение вариантов раскладки частей на выгруженном снимке (без БД): жадная эвристика и CP-SAT с разными весами.
   python -m tools.compare_snapshot --dir <папка снимка> [--profile profiles/ozon.toml]"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import time
import tomllib
from collections import Counter
from pathlib import Path

from gluing.ozon.loader import build_designs, load_rolls, skus_from_rows
from gluing.ozon.metrics import churn, quality
from gluing.ozon.tree import build


def load(dir_: Path, prof: dict):
    rows = list(csv.DictReader(open(dir_ / "ozon_rows.csv", encoding="utf-8", newline="")))
    rolls = load_rolls(dir_ / "rolls.csv")
    skus, accounts = skus_from_rows(rows, rolls)
    designs, unknown = build_designs(skus, accounts, prof)
    sigs = json.loads((dir_ / "sigs.json").read_text(encoding="utf-8"))
    for d in designs:
        d.sigs = frozenset(tuple((a, tuple(v)) for a, v in sigs[f"{d.account}|{s.offer_id}"]) for s in d.skus
                           if sigs.get(f"{d.account}|{s.offer_id}"))
    prev = json.loads((dir_ / "prev_state.json").read_text(encoding="utf-8")) or None
    return designs, prev


def run(designs, prof, prev, label, solver):
    p = {**prof, "solver": solver}
    t = time.time()
    groups, log, state = build(designs, p, prev)
    dt = time.time() - t
    by = {}
    for g in groups:
        if g.kind == "склейка":
            by.setdefault((g.account, g.brand, g.typ, g.leaf), []).append(g)
    multi = {k: v for k, v in by.items() if len(v) > 1}

    def pw(gr):
        return sum(d.power for d in gr.designs)

    ratio = {k: max(pw(x) for x in v) / max(1.0, min(pw(x) for x in v)) for k, v in multi.items()}
    nomix = sum(1 for v in multi.values() for x in v
                if not (any(d.power >= 1 for d in x.designs) and any(d.power == 0 for d in x.designs)))
    clash = sum(1 for g in groups for i, a in enumerate(g.designs) for b in g.designs[i + 1:] if a.sigs & b.sigs)
    gaps = [max(len(x.designs) for x in v) - min(len(x.designs) for x in v) for v in multi.values()]
    q = quality(groups)
    ch = churn(prev, state) if prev else {"сменили склейку": 0, "доля": 0}
    return dict(label=label, time=dt, q=q, ch=ch, multi=len(multi), parts=sum(len(v) for v in multi.values()), ratio=ratio,
                ratio_mean=st.mean(ratio.values()) if ratio else 0, ratio_max=max(ratio.values()) if ratio else 0,
                gap=st.mean(gaps) if gaps else 0, nomix=nomix, clash=clash, state=state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--profile", default="profiles/ozon.toml")
    a = ap.parse_args()
    prof = tomllib.load(open(a.profile, "rb"))
    meta = json.loads((Path(a.dir) / "meta.json").read_text(encoding="utf-8"))
    designs, prev = load(Path(a.dir), prof)
    print("снимок:", meta, "| дизайнов:", len(designs), "| вчерашнее состояние:", "есть" if prev else "нет")
    W = prof["solver"]["weights"]
    variants = [("жадная", {**prof["solver"], "parts": "greedy"})]
    for mv, bal in ((60, 2), (150, 2), (300, 2), (150, 6)):
        variants.append((f"CP-SAT move={mv} balance={bal}", {**prof["solver"], "parts": "cpsat", "weights": {**W, "move": mv, "balance": bal}}))
    res = [run(designs, prof, prev, n, s) for n, s in variants]
    print(f"веток с несколькими частями: {res[0]['multi']}, частей в них: {res[0]['parts']}")
    print(" | ".join(["показатель"] + [r["label"] for r in res]))
    rows = [("время расчёта, с", lambda r: f"{r['time']:.1f}"), ("склеек", lambda r: r["q"]["склеек"]),
            ("малых склеек", lambda r: r["q"]["малых склеек"]),
            ("доля склеек с хитом и неликвидом", lambda r: r["q"]["доля склеек с хитом и неликвидом"]),
            ("разброс силы склеек (CV)", lambda r: r["q"]["разброс силы склеек (CV)"]),
            ("сильная/слабая часть, среднее", lambda r: f"{r['ratio_mean']:.2f}"),
            ("сильная/слабая часть, максимум", lambda r: f"{r['ratio_max']:.2f}"),
            ("разница размеров частей, среднее", lambda r: f"{r['gap']:.2f}"),
            ("частей без хита или без неликвида", lambda r: r["nomix"]),
            ("пар близнецов в одной группе", lambda r: r["clash"]),
            ("сменили склейку за день", lambda r: f"{r['ch']['сменили склейку']} ({r['ch']['доля']:.1%})")]
    for label, f in rows:
        print(f"{label} | " + " | ".join(str(f(r)) for r in res))
    base, best = res[0], res[2]                                  # жадная и CP-SAT move=150
    print("--- ветки, где CP-SAT (move=150) заметно улучшил баланс силы (сильная/слабая часть):")
    diffs = sorted(((base["ratio"][k] - best["ratio"][k], k) for k in base["ratio"] if k in best["ratio"]), reverse=True)
    for d, k in diffs[:8]:
        print(f"   {k[1]} | {k[2]} | {k[3]}: {base['ratio'][k]:.2f} -> {best['ratio'][k]:.2f}")
    print("веток, где баланс стал хуже:", len([1 for d, k in diffs if d < -0.05]))
    moved = Counter()
    for uid, part in best["state"]["assign"].items():
        old = (prev or {}).get("assign", {}).get(uid)
        if old and old != part:
            moved[uid.split("|")[2]] += 1
    print("смены склейки у CP-SAT по типам:", dict(moved.most_common(6)))


if __name__ == "__main__":
    main()
