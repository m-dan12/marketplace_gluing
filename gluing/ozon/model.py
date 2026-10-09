from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass
class OzonSku:
    offer_id: str
    title: str
    category: str
    stock: int
    orders28: int
    prefix: str
    design_no: str          # цифры дизайна
    key: str
    key_nums: tuple[str, ...]
    stock_roll: int = 0         # отрезы из рулона (только ткани): метры рулона // длина отреза
    roll_code: str = ""         # код рулона, например PT110
    roll_m: int = 0             # метры рулона на складе FBS Селсапа
    cut_m: int = 0              # длина отреза в метрах


@dataclass
class Design:
    """Единица склейки Озона: все SKU одного дизайна внутри бренда и типа."""
    account: str
    brand: str
    typ: str
    design: str                                   # префикс + цифры, например PT1423
    digits: str                                   # только цифры (для списков эко и детских)
    skus: list[OzonSku] = field(default_factory=list)
    features: Counter = field(default_factory=Counter)   # признаки SKU
    feature: str = ""                             # признак дизайна в дереве

    @property
    def uid(self) -> str:
        return f"{self.account}|{self.brand}|{self.typ}|{self.design}"

    @property
    def orders(self) -> int:
        return sum(s.orders28 for s in self.skus)


@dataclass
class OGroup:
    key: str                  # стабильный ключ: кабинет|бренд|тип|ветка|номер части
    account: str
    brand: str
    typ: str
    leaf: str                 # ветка дерева, например «Рюши › Детские»
    idx: int                  # номер части (если ветка больше 18 дизайнов)
    designs: list[Design]
    kind: str                 # «склейка» | «малая» | «одиночка»
