"""Отчёт по живым склейкам WB (только чтение БД): есть ли склейки с одним дизайном в нескольких размерах (многоуровневые),
дубли цвета внутри склейки и распределение размеров. Нужен, чтобы выяснить, почему многоуровневая склейка не получается.

    python -m tools.wb_multilevel_report [--account skazka]"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict

import psycopg

from gluing.parsing import parse_article
from plan_ozon import database_url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="")
    a = ap.parse_args()
    with psycopg.connect(database_url()) as conn, conn.cursor() as cur:
        cur.execute("select account, imt_id, nm_id, vendor_code, subject, color from gluing.wb_card"
                    + (" where account=%s" if a.account else ""), (a.account,) if a.account else ())
        rows = cur.fetchall()
        conn.rollback()
    imts: dict[tuple, list[tuple]] = defaultdict(list)
    for acc, imt, nm, vc, subj, color in rows:
        p = parse_article(vc)
        imts[(acc, imt)].append((nm, vc, p["design"], p["key"], color or "", subj))
    sizes = Counter(len(v) for v in imts.values())
    multi, dup_color, mixed_sizes = [], [], 0
    for k, v in imts.items():
        if len(v) < 2:
            continue
        by_design = defaultdict(set)
        for nm, vc, d, key, color, _ in v:
            by_design[d].add(key)
        if any(len(s) > 1 for s in by_design.values()):
            multi.append(k)
        if len({key for _, _, _, key, _, _ in v}) > 1:
            mixed_sizes += 1
        colors = Counter(c for *_, c, _ in v if c)
        if any(n > 1 for n in colors.values()):
            dup_color.append(k)
    print(f"склеек {len(imts)}, карточек {len(rows)}; размеры склеек: " + ", ".join(f"{n}→{c}" for n, c in sorted(sizes.items())[:12]))
    print(f"склеек с разными ключами размера: {mixed_sizes}")
    print(f"многоуровневых (один дизайн в нескольких размерах): {len(multi)}")
    for k in multi[:5]:
        print("  ", k, [(vc, color) for _, vc, _, _, color, _ in imts[k]][:6])
    print(f"склеек с повторяющимся цветом: {len(dup_color)}")
    for k in dup_color[:5]:
        print("  ", k, [(vc, color) for _, vc, _, _, color, _ in imts[k]][:6])


if __name__ == "__main__":
    main()
