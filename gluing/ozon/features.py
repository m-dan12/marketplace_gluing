"""Признаки SKU по типам товара Озона и запасные ветки: определения лежат в профиле (profiles/ozon.toml, [features.types]).

Признак — свойство SKU, а склеиваем дизайнами. При split_design = true дизайн делится по признаку (рюшевые артикулы идут
в свою ветку, обычные в свою); при false признак дизайна — самый редкий из признаков его SKU (RARITY), дизайн целиком.
Простыни по резинке не делятся: резинка и классика — варианты одного дизайна.

Правило признака: {name, key/title/offer/prefix = регулярные выражения}; условия внутри правила объединяются «и»,
правила идут по порядку, срабатывает первое, иначе default типа. Код под отдельные признаки не нужен.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .model import Design, OzonSku

DEFAULT_PROFILE = Path(__file__).resolve().parents[2] / "profiles" / "ozon.toml"
FIELDS = ("key", "title", "offer", "prefix")


class Rules:
    """Скомпилированные определения признаков из профиля."""

    def __init__(self, features: dict):
        self.bed = set(features.get("bed_types", []))
        self.rarity = list(features.get("rarity", []))
        self.default: dict[str, str] = {}
        self.fallback: dict[str, dict[str, str]] = {}
        self._rules: dict[str, list[tuple[str, dict[str, re.Pattern]]]] = {}
        for typ, cfg in (features.get("types") or {}).items():
            self.default[typ] = cfg["default"]
            self.fallback[typ] = dict(cfg.get("fallback", {}))
            rules = []
            for r in cfg.get("rules", []):
                unknown = set(r) - {"name", *FIELDS}
                if unknown:
                    raise ValueError(f"{typ}: неизвестные поля правила {sorted(unknown)}")
                conds = {f: re.compile(r[f], re.IGNORECASE) for f in FIELDS if f in r}
                if not conds:
                    raise ValueError(f"{typ}: у правила «{r['name']}» нет условий")
                rules.append((r["name"], conds))
            self._rules[typ] = rules

    @classmethod
    def from_profile(cls, profile: dict) -> "Rules":
        f = profile.get("features", {})
        if "types" not in f:                                  # профиль без определений: берём определения по умолчанию
            f = {**_default_features(), **f}
        return cls(f)

    def feature(self, typ: str, s: OzonSku) -> str:
        if typ not in self.default:
            return "—"
        src = {"key": s.key, "title": s.title or "", "offer": s.offer_id, "prefix": s.prefix}
        for name, conds in self._rules[typ]:
            if all(rx.search(src[f]) for f, rx in conds.items()):
                return name
        return self.default[typ]

    def pick(self, d: Design) -> str:
        """Признак дизайна: самый редкий из признаков его SKU."""
        present = set(d.features)
        for f in self.rarity:
            if f in present:
                return f
        return self.default.get(d.typ, "—")


def _default_features() -> dict:
    with open(DEFAULT_PROFILE, "rb") as f:
        return tomllib.load(f)["features"]


_RULES = Rules(_default_features())

# Прежние имена для старых скриптов (ops/) и метрик: определения берутся из профиля по умолчанию.
BED = _RULES.bed
DEFAULT = _RULES.default
FALLBACK = _RULES.fallback
RARITY = _RULES.rarity


def sku_feature(typ: str, s: OzonSku, rules: Rules | None = None) -> str:
    return (rules or _RULES).feature(typ, s)


def pick_feature(d: Design, rules: Rules | None = None) -> str:
    return (rules or _RULES).pick(d)
