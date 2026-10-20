"""Решение об автоматическом применении плана (чистая функция, без БД и сети).

Автоприменение разрешено, только если одновременно: оно включено в профиле ([autoapply] enabled), нет стоп-файла, предполётный
отчёт не содержит блокировок, не идёт предыдущее применение, число операций не больше лимита. Пустой план применять нечего.
Любое «нет» превращается в событие apply_blocked с причинами, а не в тихий пропуск."""
from __future__ import annotations

from dataclasses import dataclass, field

from applier.preflight import Report


@dataclass
class Config:
    enabled: bool = False
    max_ops: int = 3000
    kill_file: str = "logs/AUTOAPPLY_OFF"        # если файл существует, автоприменение остановлено (аварийный выключатель)


@dataclass
class Decision:
    action: str                                  # apply | skip_empty | blocked | off
    reasons: list[str] = field(default_factory=list)


def load_config(profile: dict) -> Config:
    c = profile.get("autoapply") or {}
    return Config(bool(c.get("enabled", False)), int(c.get("max_ops", 3000)), str(c.get("kill_file", "logs/AUTOAPPLY_OFF")))


def decide(report: Report, n_ops: int, cfg: Config, killed: bool, running: bool) -> Decision:
    if not cfg.enabled:
        return Decision("off", ["автоприменение выключено в профиле"])
    reasons: list[str] = []
    if killed:
        reasons.append(f"стоит аварийный выключатель ({cfg.kill_file})")
    reasons += [f"{i.name}: {i.detail}" for i in report.stops]
    if running:
        reasons.append("предыдущее применение ещё идёт")
    if n_ops > cfg.max_ops:
        reasons.append(f"операций {n_ops}, лимит автоприменения {cfg.max_ops}")
    if reasons:
        return Decision("blocked", reasons)
    if n_ops == 0:
        return Decision("skip_empty")
    return Decision("apply")
