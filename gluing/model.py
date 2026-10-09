from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Card:
    """Одна карточка (SKU) с остатком: единый формат для всех правил."""
    article: str                  # артикул продавца, например PT5948/0-0-20/1
    nm_id: int | None = None      # артикул WB (для Озона не используется)
    barcode: str = ""
    title: str = ""
    category: str = ""
    brand: str = ""
    prefix: str = ""              # PT, AM, MT...
    design: str = ""              # цифры до первого слеша, у компаньонов 8 цифр
    key: str = ""                 # 0-0-26, 1-500220
    key_nums: tuple[str, ...] = ()
    stock_wb: int = 0
    stock_fbs: int = 0
    stock_roll: int = 0           # расчётное наличие из рулона (только ткани)
    orders30: int = 0
    rub30: int = 0
    roll_code: str = ""           # PT110 для тканей
    roll_len_m: int = 0           # длина отреза в метрах
    flags: set[str] = field(default_factory=set)

    @property
    def stock_total(self) -> int:
        return self.stock_wb + self.stock_fbs + self.stock_roll


@dataclass
class Group:
    gid: str
    brand: str
    category: str
    rule: str                      # название сработавшего правила
    cards: list[Card] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # причины и предупреждения
