"""Простой отчёт «рекомендации подобрались нормально»: проверяется ФАКТИЧЕСКОЕ состояние на WB (/list после загрузки), а не наш расчёт.

Проверки: у каждой карточки с остатком есть рекомендации (и их полные 20); среди рекомендуемых нет артикулов без остатка; правила выполняются
(не сама на себя, не из своей склейки, без дублей, бренд/эко/категория той же группы, как при подборе). Отчёт строится только после успешного
заполнения (решает вызывающий код), поэтому устаревшего отчёта по прошлому запуску не бывает."""
from __future__ import annotations

from dataclasses import dataclass, field

import openpyxl

from gluing.model import Card
from recommend.wb import _pool_key, group_of

TITLES = {"skazka": "Сказка", "milky_garden": "Milky Garden", "timeless": "Timeless"}
MAX_ROWS = 5000                                                   # строк нарушений в файле


@dataclass
class Report:
    targets: int = 0
    no_recs: list[int] = field(default_factory=list)              # карточки с остатком без единой рекомендации
    short: list[int] = field(default_factory=list)                # меньше положенного числа (но не ноль)
    no_stock: list[tuple[int, int]] = field(default_factory=list)  # (карточка, рекомендуемый без остатка)
    rules: list[tuple[int, int, str]] = field(default_factory=list)  # (карточка, рекомендуемый, что нарушено)

    @property
    def ok(self) -> bool:
        return not (self.no_recs or self.no_stock or self.rules)


def build_report(actual: dict[int, list[int]], cards: dict[int, Card], imt: dict[int, int], prof: dict, eco: frozenset[str]) -> Report:
    """actual — {nm_id: [рекомендуемые]} с WB; cards — карточки с остатком кабинета."""
    total = prof["recommend"]["total"]
    rep = Report()
    for t, c in sorted(cards.items()):
        g = group_of(c, prof)
        if g is None:
            continue
        rep.targets += 1
        lst = actual.get(t) or []
        if not lst:
            rep.no_recs.append(t)
            continue
        if len(lst) < total:
            rep.short.append(t)
        if len(set(lst)) != len(lst):
            rep.rules.append((t, t, "дубль в списке"))
        key = _pool_key(c, g, prof, eco)
        for r in dict.fromkeys(lst):
            rc = cards.get(r)
            if rc is None or rc.stock_total <= 0:
                rep.no_stock.append((t, r))
                continue
            if r == t:
                rep.rules.append((t, r, "карточка рекомендует саму себя"))
            elif imt.get(t) is not None and imt.get(t) == imt.get(r):
                rep.rules.append((t, r, "из своей склейки"))
            elif group_of(rc, prof) != g:
                rep.rules.append((t, r, "другая группа категорий"))
            else:
                rk = _pool_key(rc, g, prof, eco)
                if rk != key:
                    rep.rules.append((t, r, "бренд" if g == "bedding" and rk[1] != key[1] else "эко"))
    return rep


def summary(acc: str, rep: Report, stats: dict) -> tuple[str, str, str]:
    """(уровень, заголовок, текст) для дашборда."""
    mark = lambda n: "✓" if not n else "✗"
    lines = [
        f"{mark(0)} Загружено в Джем: {stats.get('changed', 0)} карточек, совпало на WB {stats.get('matched', 0)}" if stats.get("changed")
        else f"{mark(0)} Изменений не потребовалось: на WB уже стоят актуальные рекомендации",
        f"{mark(rep.no_recs)} Карточек с остатком без рекомендаций: {len(rep.no_recs)} из {rep.targets}",
        f"{mark(rep.no_stock)} Рекомендаций на артикулы без остатка: {len(rep.no_stock)}",
        f"{mark(rep.rules)} Нарушений правил (бренд, эко, склейка, категории): {len(rep.rules)}",
    ]
    if rep.short:
        lines.append(f"Неполных (меньше 20): {len(rep.short)}")
    title = f"Рекомендации WB, {TITLES.get(acc, acc)}: " + ("всё в порядке" if rep.ok else "есть замечания")
    return ("ok" if rep.ok else "warn"), title, "\n".join(lines)


def write_xlsx(path, acc: str, rep: Report, cards: dict[int, Card], text: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Итог"
    for line in text.split("\n"):
        ws.append([line])
    ws2 = wb.create_sheet("Замечания")
    ws2.append(["Что не так", "Карточка (nm)", "Артикул", "Рекомендуемый (nm)", "Артикул", "Пояснение"])
    art = lambda n: cards[n].article if n in cards else "нет остатка / не найден"
    rows = [("нет рекомендаций", t, art(t), "", "", "") for t in rep.no_recs]
    rows += [("рекомендован артикул без остатка", t, art(t), r, art(r), "") for t, r in rep.no_stock]
    rows += [("нарушено правило", t, art(t), r, art(r), why) for t, r, why in rep.rules]
    for row in rows[:MAX_ROWS]:
        ws2.append(list(row))
    if len(rows) > MAX_ROWS:
        ws2.append([f"... и ещё {len(rows) - MAX_ROWS}"])
    wb.save(path)
