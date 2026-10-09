"""Рисует дерево разбиений Озона в draw.io: python tools/make_drawio.py docs/ozon_tree.drawio

Дерево описано данными (Node), раскладка и XML строятся автоматически.
Цвета: серый — вход, синий — жёсткий уровень, зелёный — мягкий (по порогу), жёлтый — итоговая склейка, красный — не склеиваем.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

STYLES = {
    "root": "fillColor=#f5f5f5;strokeColor=#666666;fontColor=#333333;",
    "hard": "fillColor=#dae8fc;strokeColor=#6c8ebf;",
    "soft": "fillColor=#d5e8d4;strokeColor=#82b366;",
    "leaf": "fillColor=#fff2cc;strokeColor=#d6b656;",
    "stop": "fillColor=#f8cecc;strokeColor=#b85450;",
    "note": "fillColor=#ffffff;strokeColor=#999999;dashed=1;align=left;spacingLeft=6;fontSize=11;",
}
COL_W, COL_GAP, ROW_H, NODE_H = 230, 70, 56, 46


@dataclass
class Node:
    label: str
    kind: str = "soft"
    children: list["Node"] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # пояснения под узлом (пунктирные рамки)
    h: int = NODE_H
    x: float = 0
    y: float = 0
    depth: int = 0
    style: str = ""         # свой стиль вместо стиля по kind (для раскраски веток)
    line: str = ""          # цвет линии к родителю; если задан — рисуем плавную линию как в mindmap


def layout(node: Node, depth: int, cursor: list[float], gap: int = COL_GAP, x0: float = 0) -> None:
    node.depth = depth
    node.x = x0 + depth * (COL_W + gap)
    if not node.children:
        node.y = cursor[0]
        cursor[0] += max(node.h, ROW_H) + 8
        return
    for c in node.children:
        layout(c, depth + 1, cursor, gap, x0)
    node.y = (node.children[0].y + node.children[-1].y) / 2


class Page:
    def __init__(self, name: str):
        self.name, self.cells, self.n = name, [], 1

    def _id(self, p: str) -> str:
        self.n += 1
        return f"{p}{self.n}"

    def vertex(self, text: str, x: float, y: float, w: int, h: int, style: str) -> str:
        cid = self._id("v")
        val = escape(text).replace("\n", "&lt;br&gt;")
        self.cells.append(
            f'<mxCell id="{cid}" value="{val}" style="rounded=1;whiteSpace=wrap;html=1;{style}" vertex="1" parent="1">'
            f'<mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def edge(self, a: str, b: str, color: str = "") -> None:
        cid = self._id("e")
        if color:      # mindmap: плавная линия цвета ветки без стрелки
            look = f"edgeStyle=entityRelationEdgeStyle;curved=1;html=1;endArrow=none;strokeWidth=2;strokeColor={color};"
        else:
            look = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;"
                    "exitX=1;exitY=0.5;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;strokeColor=#666666;")
        self.cells.append(
            f'<mxCell id="{cid}" style="{look}" edge="1" parent="1" source="{a}" target="{b}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>')

    def tree(self, root: Node, y0: float = 90, headers: list[str] | None = None, gap: int = COL_GAP, x0: float = 0) -> None:
        layout(root, 0, [y0], gap, x0)
        ids: dict[int, str] = {}

        def draw(nd: Node):
            ids[id(nd)] = self.vertex(nd.label, nd.x, nd.y, COL_W, nd.h, nd.style or STYLES[nd.kind])
            for i, note in enumerate(nd.notes):
                self.vertex(note, nd.x, nd.y + nd.h + 8 + i * 0, COL_W, 58, STYLES["note"])
            for c in nd.children:
                draw(c)
                self.edge(ids[id(nd)], ids[id(c)], c.line)

        draw(root)
        for i, h in enumerate(headers or []):
            self.vertex(h, x0 + i * (COL_W + gap), 20, COL_W, 36,
                        "fillColor=none;strokeColor=none;fontStyle=1;fontSize=13;align=center;")

    def xml(self, idx: int) -> str:
        return (f'<diagram id="p{idx}" name="{escape(self.name)}"><mxGraphModel dx="1400" dy="900" grid="1" gridSize="10" '
                f'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" math="0" shadow="0">'
                f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(self.cells)}</root></mxGraphModel></diagram>')


def L(label, kind="leaf", h=NODE_H, children=None, notes=None) -> Node:
    return Node(label, kind, children or [], notes or [], h)


def feature(label, small_to=None):
    text = label + (f"\nесли мало → в «{small_to}»" if small_to else "")
    return L(text + "\n→ затем блок «Детские / эко»", "leaf", 60)


def page_general() -> Page:
    p = Page("1. Общая схема Озон")
    leaves = [
        L("Склейка\n6–18 дизайнов", "leaf"),
        L("Больше 18 дизайнов →\nделим на равные части\n(поровну и по силе, веером)", "leaf", 60),
        L("Меньше 6 дизайнов →\nсписок «слишком мало»", "stop"),
    ]
    chain = L("Карточки Озона с остатком\nFBO + FBS > 1", "root", 52, [
        L("1. Бренд — жёстко\nпо префиксу артикула", "hard", 52, [
            L("2. Тип товара — жёстко\nиз карточки Озона", "hard", 52, [
                L("3. Признаки типа\nпо порогу", "soft", 52, [
                    L("4. Детские / эко\nпо порогу, только бельё", "soft", 52, [
                        L("5. Размер группы\n6–18 дизайнов", "soft", 52, leaves)],
                        notes=[])],
                    )],
                    )],
                    )])
    brands = ("Бренды (жёстко, по префиксу):\nСказка — PT (и опечатка РТ), GR, OK\nМечта — MT\nАнна Мария — AM\n"
              "Анна Мария — PA (отдельно от AM)\nСказка Сатин — PC\nMilky Garden — MY, MI, KK\nTimeless — без префикса")
    rule = ("Правило порога: признак отделяется, только если получившаяся подгруппа ≥ порога "
            "(Озон: 6 дизайнов, WB: 10 SKU). Иначе она остаётся в «обычных», а когда товаров станет больше — "
            "отделится сама.")
    why = ("Почему тип и бренд жёсткие: Озон склеивает только товары одного типа и бренда (так сказано в ozon_skleyki.py). "
           "Типы и бренды не смешиваем.")
    p.tree(chain, 60, None, gap=120)
    # пояснения под цепочкой
    p.vertex(brands, 350, 330, 360, 150, STYLES["note"])
    p.vertex(rule, 740, 330, 380, 110, STYLES["note"])
    p.vertex(why, 1150, 330, 380, 100, STYLES["note"])
    p.vertex("Цвета: серый — вход, синий — жёсткий уровень, зелёный — мягкий (по порогу), жёлтый — склейка, красный — не склеиваем",
             0, 520, 900, 30, "fillColor=none;strokeColor=none;fontSize=11;align=left;")
    return p


def page_bedding() -> Page:
    p = Page("2. Постельное бельё")
    root = L("Постельное бельё\n(типы товара внутри бренда)", "root", 52, [
        L("Комплект постельного белья", "hard", 46, [
            feature("Обычные"), feature("Рюши\n(17, 18 Сказка; 19 Timeless)", "Обычные"), feature("Ромбы", "Обычные")]),
        L("Пододеяльник", "hard", 46, [
            feature("Обычные (на молнии)"), feature("Ромбы", "Обычные"), feature("Кант", "Обычные")]),
        L("Простыня", "hard", 46, [
            feature("Обычные"), feature("На резинке"), feature("С оборками", "Обычные")]),
        L("Наволочка", "hard", 46, [
            feature("Обычные"), feature("Кант", "Обычные"), feature("Рюши", "Обычные"), feature("Кружево", "Рюши"),
            feature("Пуговицы", "Обычные")]),
        L("Наволочка декоративная\n(другой тип)", "hard", 46, [L("Без признаков → склейка", "leaf")]),
    ])
    p.tree(root, 90, ["Категория", "2. Тип (жёстко)", "3. Признак (по порогу)"], gap=110)
    block = L("Блок «Детские / эко»\nодин и тот же под каждым признаком", "root", 56, [
        L("Детские\nесли ≥ порога дизайнов", "soft", 50, [L("Склейка детских", "leaf")]),
        L("Эко (из оставшихся)\nесли ≥ порога дизайнов", "soft", 50, [L("Склейка эко", "leaf")]),
        L("Остальные", "soft", 50, [L("Склейка обычных", "leaf")]),
    ])
    x0 = 3 * (COL_W + 110)
    p.tree(block, 250, None, gap=70, x0=x0)
    p.vertex("Порядок «детские, потом эко» — как в вашем наброске. Дизайн, который и детский, и эко "
             "(например 6371), попадёт в детские. У GPT было наоборот (эко приоритетнее): решить.",
             x0, 640, 3 * COL_W + 140, 70, STYLES["note"])
    p.vertex("Кроме бренда и типа (жёстко) каждое деление — по порогу: подгруппа отделяется только если в ней ≥ 6 дизайнов; "
             "если меньше — остаётся в родителе.", x0, 730, 3 * COL_W + 140, 60, STYLES["note"])
    return p


def page_other() -> Page:
    p = Page("3. Остальные типы")
    stop = lambda t: L(t, "stop", 52)  # noqa: E731
    root = L("Прочие типы товара\n(внутри бренда)", "root", 52, [
        L("Штора", "hard", 46, [
            L("Обычные", "leaf"), L("На липучках\nесли мало → в Обычные", "leaf", 50),
            L("Уличные (OK, GR)\nесли мало → в Обычные", "leaf", 50)]),
        L("Ткань (Материал для рукоделия)", "hard", 46, [
            L("Без признаков, без детских и эко\nгруппы по 18 дизайнов", "leaf", 56)]),
        L("Скатерть", "hard", 46, [L("Без признаков", "leaf")]),
        L("Карнавальная одежда", "hard", 46, [
            L("Сарафаны", "leaf"), L("Юбки", "leaf"), L("Плащи", "leaf")]),
        L("Покрывало", "hard", 46, [L("Без признаков", "leaf")]),
        L("Дорожка для стола, пеленка,\nполотенце, сумка, автогамак,\nмешок для подарков", "hard", 60, [
            stop("Меньше 6 дизайнов:\nне склеиваем, в список\n(если станет больше — склеятся)")]),
    ])
    p.tree(root, 90, ["Категория", "2. Тип (жёстко)", "3. Признак (по порогу)"], gap=110)
    return p


# ---------- одно большое дерево (mindmap: слева корень, ветви вправо) ----------
FINAL_BEDDING = "Детские → Эко → остальные\nкаждое, если ≥ порога\nзатем размер 6–18"


def _feat(label: str, small_to: str | None = None, final: str = FINAL_BEDDING, kind: str = "soft") -> Node:
    text = label + (f"\nмало → в «{small_to}»" if small_to else "")
    return Node(text, kind, [Node(final, "leaf", h=58)], h=56)


def bedding_types() -> list[Node]:
    return [
        Node("Комплект постельного белья", "hard", [
            _feat("Обычные"), _feat("Рюши (17, 18 Сказка;\n19 Timeless)", "Обычные"), _feat("Ромбы", "Обычные")]),
        Node("Пододеяльник", "hard", [
            _feat("Обычные (на молнии)"), _feat("Ромбы", "Обычные"), _feat("Кант", "Обычные")]),
        Node("Простыня", "hard", [
            _feat("Обычные"), _feat("На резинке"), _feat("С оборками", "Обычные")]),
        Node("Наволочка", "hard", [
            _feat("Обычные"), _feat("Кант", "Обычные"), _feat("Рюши", "Обычные"), _feat("Кружево", "Рюши"),
            _feat("Пуговицы", "Обычные")]),
    ]


def other_types() -> list[Node]:
    plain = "Склейка\n6–18 дизайнов"
    return [
        Node("Наволочка декоративная", "hard", [_feat("Без признаков", final=plain)]),
        Node("Штора", "hard", [
            _feat("Обычные", final=plain), _feat("На липучках", "Обычные", plain),
            _feat("Уличные (OK, GR)", "Обычные", plain)]),
        Node("Ткань", "hard", [_feat("Без признаков", final="Без детских и эко\nгруппы по 18 дизайнов")]),
        Node("Скатерть", "hard", [_feat("Без признаков", final=plain)]),
        Node("Карнавальная одежда", "hard", [
            _feat("Сарафаны", final=plain), _feat("Юбки", final=plain), _feat("Плащи", final=plain)]),
        Node("Покрывало", "hard", [_feat("Без признаков", final=plain)]),
    ] + [Node(t, "hard", [_feat("Меньше 6 дизайнов", kind="stop", final="Не склеиваем:\nсписок «слишком мало»")])
         for t in TINY_TYPES]


BRANCH_COLORS = [          # (заливка, линия) для каждого бренда
    ("#dae8fc", "#6c8ebf"), ("#e1d5e7", "#9673a6"), ("#ffe6cc", "#d79b00"), ("#fad9d5", "#ae4132"),
    ("#d5e8d4", "#82b366"), ("#c3ede8", "#2a9d8f"), ("#fff2cc", "#d6b656"),
]


def paint(node: Node, fill: str, line: str) -> None:
    """Красим ветку цветом бренда; пунктир — уровень «по порогу»; красные узлы «не склеиваем» не трогаем."""
    for c in node.children:
        if c.kind != "stop":
            dash = "dashed=1;" if c.kind == "soft" else ""
            bold = "fontStyle=1;" if c.kind == "hard" else ""
            c.style = f"fillColor={fill};strokeColor={line};{dash}{bold}fontSize=11;"
        c.line = line
        paint(c, fill, line)


def page_full() -> Page:
    p = Page("Озон: полное дерево")
    brands = [
        ("Сказка\nPT (и опечатка РТ), GR, OK", True),
        ("Мечта\nMT", False), ("Анна Мария\nAM", False), ("Анна Мария\nPA (отдельно от AM)", False),
        ("Сказка Сатин\nPC", False), ("Milky Garden\nMY, MI, KK", False), ("Timeless\nбез префикса", False),
    ]
    kids = []
    for (label, extended), (fill, line) in zip(brands, BRANCH_COLORS):
        types = bedding_types() + (other_types() if extended else [])
        br = Node(label, "hard", types, h=52)
        br.style = f"fillColor={fill};strokeColor={line};fontStyle=1;fontSize=12;"
        br.line = line
        paint(br, fill, line)
        kids.append(br)
    root = Node("Карточки Озона\nс остатком\nFBO + FBS > 1", "root", kids, h=70)
    root.style = STYLES["root"] + "fontStyle=1;fontSize=13;"
    p.tree(root, 90, ["Вход", "1. Бренд\n(жёстко)", "2. Тип товара\n(жёстко)", "3. Признак типа\n(по порогу)",
                      "4. Итог: детские, эко, размер"], gap=70)
    p.vertex("Как читать: цвет ветки — бренд. Сплошная рамка — жёсткое деление (бренд, тип), пунктир — деление по порогу: "
             "подгруппа отделяется, только если в ней достаточно дизайнов (Озон: 6), иначе остаётся в родителе. "
             "Красный — не склеиваем. Бельё (комплект, пододеяльник, простыня, наволочка) показано у каждого бренда; "
             "остальные типы — у Сказки, у других брендов они добавятся, если появятся такие товары.",
             0, -80, 1480, 70, "fillColor=none;strokeColor=none;fontSize=11;align=left;")
    return p


# ---------- этапы слева направо: блок на этап, внутри все варианты этапа ----------
def _box(p: Page, title: str, x: float, y: float, w: int, h: int) -> str:
    return p.vertex(title, x, y, w, h,
                    "fillColor=#fafafa;strokeColor=#888888;arcSize=3;verticalAlign=top;fontStyle=1;fontSize=13;spacingTop=6;")


TINY_TYPES = ["Дорожка для стола", "Пеленка для малышей", "Полотенце для ванной", "Сумка",
              "Автогамак для животных", "Мешок для подарков"]     # отдельные типы: между собой не склеиваются


def page_stages() -> Page:
    p = Page("Озон: этапы")
    bw = {"root": 190, "b1": 240, "b2": 260, "b3": 300, "b4": 270, "b5": 280}
    gap = 70
    bx, x = {}, 0
    for k in ("root", "b1", "b2", "b3", "b4", "b5"):
        bx[k] = x
        x += bw[k] + gap

    # этап 3: признаки, сгруппированы по типу
    groups = [
        ("Комплект постельного белья", [("Обычные", ""), ("Рюши (17, 18 Сказка; 19 Timeless)", "Обычные"),
                                         ("Ромбы", "Обычные")], "soft"),
        ("Пододеяльник", [("Обычные (на молнии)", ""), ("Ромбы", "Обычные"), ("Кант", "Обычные")], "soft"),
        ("Простыня", [("Обычные", ""), ("На резинке", ""), ("С оборками", "Обычные")], "soft"),
        ("Наволочка", [("Обычные", ""), ("Кант", "Обычные"), ("Рюши", "Обычные"), ("Кружево", "Рюши"),
                     ("Пуговицы", "Обычные")], "soft"),
        ("Штора", [("Обычные", ""), ("На липучках", "Обычные"), ("Уличные (OK, GR)", "Обычные")], "soft"),
        ("Карнавальная одежда", [("Сарафаны", ""), ("Юбки", ""), ("Плащи", "")], "soft"),
        ("Наволочка декоративная, ткань,\nскатерть, покрывало", [("Без признаков", "")], "soft"),
    ] + [(None, [(f"{t}\nменьше 6 дизайнов", "")], "stop") for t in TINY_TYPES]
    item_h, item_gap, head_h = 40, 6, 36
    h3 = 60 + sum((head_h if title else 0) + len(fs) * (item_h + item_gap) + 8 for title, fs, _ in groups)
    top = 80

    def centered(h: int) -> float:
        return top + (h3 - h) / 2

    # корень
    root_h = 90
    p.vertex("Карточки Озона\nс остатком\nFBO + FBS > 1", bx["root"], centered(root_h), bw["root"], root_h,
             STYLES["root"] + "fontStyle=1;fontSize=13;")
    root_id = p.cells[-1].split('"')[1]

    # этап 1: бренды
    brands = ["Сказка\nPT (и опечатка РТ), GR, OK", "Мечта\nMT", "Анна Мария\nAM", "Анна Мария\nPA (отдельно от AM)",
              "Сказка Сатин\nPC", "Milky Garden\nMY, MI, KK", "Timeless\nбез префикса"]
    h1 = 60 + len(brands) * 66
    b1 = _box(p, "1. Бренд — жёстко\n(по префиксу артикула)", bx["b1"], centered(h1), bw["b1"], h1)
    y = centered(h1) + 62
    for label, (fill, line) in zip(brands, BRANCH_COLORS):
        p.vertex(label, bx["b1"] + 15, y, bw["b1"] - 30, 52, f"fillColor={fill};strokeColor={line};fontSize=11;")
        y += 66

    # этап 2: типы товара
    types = ["Комплект постельного белья", "Пододеяльник", "Простыня", "Наволочка", "Наволочка декоративная",
             "Штора", "Ткань", "Скатерть", "Карнавальная одежда", "Покрывало"] + TINY_TYPES
    h2 = 70 + len(types) * 56
    b2 = _box(p, "2. Тип товара — жёстко\n(из карточки Озона)", bx["b2"], centered(h2), bw["b2"], h2)
    y = centered(h2) + 66
    for t in types:
        p.vertex(t, bx["b2"] + 15, y, bw["b2"] - 30, 44,
                 STYLES["hard"] + ("fillColor=#f8cecc;strokeColor=#b85450;" if t in TINY_TYPES else "") + "fontSize=11;")
        y += 56

    # этап 3: признаки по типам
    b3 = _box(p, "3. Признак типа — по порогу\n(мало → остаётся в родителе)", bx["b3"], top, bw["b3"], h3)
    y = top + 56
    for title, feats, kind in groups:
        if title:
            p.vertex(title, bx["b3"] + 15, y, bw["b3"] - 30, head_h,
                     "fillColor=none;strokeColor=none;fontStyle=1;fontSize=11;align=left;")
            y += head_h
        for name, small_to in feats:
            text = name + (f"\nмало → в «{small_to}»" if small_to else "")
            style = STYLES["stop"] if kind == "stop" else STYLES["soft"] + "dashed=1;"
            p.vertex(text, bx["b3"] + 30, y, bw["b3"] - 60, item_h, style + "fontSize=10;")
            y += item_h + item_gap
        y += 8

    # этап 4: детские / эко
    h4 = 400
    b4 = _box(p, "4. Детские / эко — по порогу\n(только бельё)", bx["b4"], centered(h4), bw["b4"], h4)
    y = centered(h4) + 66
    for text, kind in (("Детские\nесли ≥ порога дизайнов", "soft"), ("Эко (из оставшихся)\nесли ≥ порога дизайнов", "soft"),
                       ("Остальные", "soft")):
        p.vertex(text, bx["b4"] + 15, y, bw["b4"] - 30, 60, STYLES[kind] + "dashed=1;fontSize=11;")
        y += 74
    p.vertex("Только комплекты, пододеяльники, простыни и наволочки. Дизайн, который и детский, и эко, "
             "попадает в детские.", bx["b4"] + 15, y + 4, bw["b4"] - 30, 90, STYLES["note"])

    # этап 5: размер группы
    h5 = 430
    b5 = _box(p, "5. Размер группы", bx["b5"], centered(h5), bw["b5"], h5)
    y = centered(h5) + 50
    for text, kind, hh in (("Склейка\n6–18 дизайнов", "leaf", 60),
                           ("Больше 18 дизайнов →\nделим пополам: поровну по числу\nдизайнов и по силе (веером)", "leaf", 78),
                           ("Меньше 6 дизайнов →\nсписок «слишком мало»", "stop", 60)):
        p.vertex(text, bx["b5"] + 15, y, bw["b5"] - 30, hh, STYLES[kind] + "fontSize=11;")
        y += hh + 14
    p.vertex("Ткань: группы по 18 дизайнов, без детских и эко.", bx["b5"] + 15, y + 6, bw["b5"] - 30, 50, STYLES["note"])

    for a, b in ((root_id, b1), (b1, b2), (b2, b3), (b3, b4), (b4, b5)):
        p.edge(a, b)
    p.vertex("Как читать: каждый блок — один этап, внутри все варианты этапа. Сплошной блок «жёстко» — деление без порога "
             "(Озон склеивает только один тип и бренд). «По порогу» — подгруппа отделяется, только если в ней "
             "достаточно дизайнов (Озон: 6), иначе остаётся в родителе и отделится сама, когда товаров станет больше.",
             0, 0, 1500, 60, "fillColor=none;strokeColor=none;fontSize=11;align=left;")
    return p


def main(out: str, full: bool = False, stages: bool = False) -> None:
    pages = [page_stages()] if stages else [page_full()] if full else [page_general(), page_bedding(), page_other()]
    xml = '<mxfile host="app.diagrams.net">' + "".join(p.xml(i) for i, p in enumerate(pages)) + "</mxfile>"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(xml, encoding="utf-8")
    print("записано:", out)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stages":
        main(sys.argv[2] if len(sys.argv) > 2 else "docs/ozon_tree_stages.drawio", stages=True)
    elif len(sys.argv) > 1 and sys.argv[1] == "--full":
        main(sys.argv[2] if len(sys.argv) > 2 else "docs/ozon_tree_full.drawio", full=True)
    else:
        main(sys.argv[1] if len(sys.argv) > 1 else "docs/ozon_tree.drawio")
