"""Запись рекомендаций продавца через API WB (content-api /api/content/v1/recommendations/*).

Перед записью текущее состояние карточек сохраняется в файл (backup), по нему можно вернуть всё как было (restore)."""
from __future__ import annotations

import json
from pathlib import Path

from applier.wb_client import WbApi

LIST, SET = "/api/content/v1/recommendations/list", "/api/content/v1/recommendations/set"
BATCH = 500                                   # карточек в одном запросе (лимит метода 5000)


def read_current(api: WbApi, nms: set[int] | None = None) -> dict[int, list[int]]:
    """{nmId: [рекомендованные nmId по порядку]} по всем карточкам кабинета (или только по nms)."""
    out: dict[int, list[int]] = {}
    nxt = 0
    while True:
        d = api.post(LIST, {"limit": 5000, "next": nxt})
        part = d.get("data") or []
        for r in part:
            if nms is None or r["nmId"] in nms:
                out[r["nmId"]] = list(r.get("recomNms") or [])
        if len(part) < 5000:
            return out
        nxt = d["next"]


def send(api: WbApi, recs: dict[int, list[int]], replace: bool = True) -> list[dict]:
    """Пишет рекомендации пачками; возвращает ошибки WB (по карточкам, которые не приняты). replace — заменить старые рекомендации."""
    errors: list[dict] = []
    items = sorted(recs.items())
    for i in range(0, len(items), BATCH):
        body = {"recList": [{"nmId": nm, "recommendations": [{"recomNm": r, "sort": k} for k, r in enumerate(lst, 1)]}
                            for nm, lst in items[i:i + BATCH]],
                "replace": replace}
        d = api.post(SET, body, write=True)
        if d.get("isError"):
            errors += d.get("errors") or []
    return errors


def backup(path: Path, current: dict[int, list[int]]) -> None:
    path.write_text(json.dumps({str(k): v for k, v in current.items()}), encoding="utf-8")


def load_backup(path: Path) -> dict[int, list[int]]:
    return {int(k): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
