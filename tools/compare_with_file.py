"""Сверка результата с готовым файлом склеек (например, от GPT за 05.10).

Показывает: сколько карточек в обоих наборах, совпадение типа правила и «чистоту» групп.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build import run  # noqa: E402


def norm_rule(rule: str) -> str:
    """Тип правила без конкретных ключей: «Ключ 0-0-26 + Ключ 0-0-28» -> «Ключ»."""
    rule = re.sub(r"^Ключ .*$", "Ключ", rule)
    rule = re.sub(r"(РОМБЫ Пододеяльники — только).*", r"\1", rule)
    return rule


def load_file(path: str) -> dict[str, tuple[str, str]]:
    ws = openpyxl.load_workbook(path, read_only=True)["Склейки"]
    rows = list(ws.iter_rows(values_only=True))
    h = rows[0]
    gi, ai, ri = h.index("ID склейки"), h.index("Артикул продавца"), h.index("Правило")
    return {r[ai]: (r[gi], r[ri]) for r in rows[1:] if r[ai]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("file")
    ap.add_argument("--fixtures", default="fixtures")
    a = ap.parse_args()
    _, cards, _, groups = run(a.profile, a.fixtures)
    ref = load_file(a.file)
    mine = {c.article: (g.gid, g.rule) for g in groups for c in g.cards}
    both = set(mine) & set(ref)
    print(f"у нас {len(mine)}, в файле {len(ref)}, общих {len(both)}")

    same_rule = sum(norm_rule(mine[x][1]) == norm_rule(ref[x][1]) for x in both)
    print(f"тип правила совпал у {same_rule} из {len(both)} ({same_rule / len(both):.0%})")
    diff = Counter((norm_rule(ref[x][1]), norm_rule(mine[x][1])) for x in both if norm_rule(mine[x][1]) != norm_rule(ref[x][1]))
    for (f, m), n in diff.most_common(12):
        print(f"   файл: {f!r:55} у нас: {m!r:45} {n}")

    # чистота: доля карточек группы, которые в файле лежат вместе с большинством
    def purity(src, dst):
        by = defaultdict(list)
        for x in both:
            by[src[x][0]].append(dst[x][0])
        tot = sum(Counter(v).most_common(1)[0][1] for v in by.values())
        return tot / len(both)

    print(f"наши группы целиком лежат в одной группе файла: {purity(mine, ref):.0%}")
    print(f"группы файла целиком лежат в одной нашей группе: {purity(ref, mine):.0%}")
    only_ours = sorted(set(mine) - set(ref))
    only_file = sorted(set(ref) - set(mine))
    print(f"только у нас {len(only_ours)}: {only_ours[:6]}")
    print(f"только в файле {len(only_file)}: {only_file[:6]}")


if __name__ == "__main__":
    main()
