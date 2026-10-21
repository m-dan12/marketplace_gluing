"""Клиент API WB для применяющего скрипта: чтение карточек (ключ чтения) и moveNm (отдельный токен для склеек).

Ключи берутся из .env: чтение — MARKETPLACE_ENV (WB_API_KEY…), токены склеек — MARKETPLACE_MERGE_ENV (WB_MERGE_TOKEN_…).
Тело запросов и ключи не логируются."""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from applier.ozon_client import read_env

BASE = "https://content-api.wildberries.ru"
ACCOUNT_ENV = {"skazka": ("WB_API_KEY", "WB_MERGE_TOKEN_SK"),
               "milky_garden": ("WB_API_KEY_MILKY", "WB_MERGE_TOKEN_MG"),
               "timeless": ("WB_API_KEY_TIMELESS", "WB_MERGE_TOKEN_TL")}
RETRY = (429, 500, 502, 503, 504)
PAUSE = 0.7                                  # лимит content-api: около 100 запросов в минуту


class WbApi:
    def __init__(self, read_key: str, merge_key: str | None = None, *, client: httpx.Client | None = None, sleep=time.sleep):
        self._read, self._merge = read_key, merge_key
        self._client = client or httpx.Client(timeout=120.0)
        self._sleep = sleep

    def post(self, path: str, body: dict, write: bool = False) -> dict:
        key = self._merge if write else self._read
        if not key:
            raise RuntimeError("нет токена для склеек (WB_MERGE_TOKEN)" if write else "нет ключа WB")
        err = ""
        for attempt in range(8):
            try:
                r = self._client.post(BASE + path, headers={"Authorization": key, "Content-Type": "application/json"}, json=body)
                if r.status_code in RETRY:
                    err = f"HTTP {r.status_code}"
                    self._sleep(20 * (attempt + 1) if r.status_code == 429 else 3 + attempt * 3)
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"{path}: HTTP {r.status_code}: {r.text[:300]}")
                self._sleep(PAUSE)
                return r.json() if r.content else {}
            except httpx.HTTPError as e:
                err = type(e).__name__
                self._sleep(3 + attempt * 3)
        raise RuntimeError(f"{path}: {err}")


def api_for(account: str, env: dict[str, str], merge_env: dict[str, str] | None = None) -> WbApi:
    if account not in ACCOUNT_ENV:
        raise SystemExit(f"неизвестный кабинет {account}: {list(ACCOUNT_ENV)}")
    rk, mk = ACCOUNT_ENV[account]
    if rk not in env:
        raise SystemExit(f"в .env нет ключа чтения кабинета {account}")
    return WbApi(env[rk], (merge_env or {}).get(mk))


ROOT = Path(__file__).resolve().parent.parent


def conf(name: str, explicit: str = "") -> str:
    """Параметр, затем переменная окружения, затем .env проекта."""
    if explicit:
        return explicit
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def merge_env_path(explicit: str = "") -> Path | None:
    p = conf("MARKETPLACE_MERGE_ENV", explicit)
    return Path(p) if p else None


def merge_accounts(merge_env: dict[str, str]) -> set[str]:
    """Кабинеты, для которых в файле токенов склеек есть токен."""
    return {acc for acc, (_, mk) in ACCOUNT_ENV.items() if merge_env.get(mk)}


def list_cards(api: WbApi) -> list[dict]:
    """Все карточки кабинета (nmID, imtID, vendorCode, subjectName, brand, title)."""
    cards: list[dict] = []
    cur: dict = {"limit": 100}
    while True:
        d = api.post("/content/v2/get/cards/list", {"settings": {"cursor": cur, "filter": {"withPhoto": -1}}})
        part = d.get("cards") or []
        cards += part
        if len(part) < 100:
            return cards
        c = d["cursor"]
        cur = {"limit": 100, "updatedAt": c["updatedAt"], "nmID": c["nmID"]}


class WbPort:
    """Реализация порта применяющего скрипта поверх WbApi."""

    def __init__(self, apis: dict[str, WbApi]):
        self.apis = apis

    def imts(self, account: str, cards: list[tuple[str, int]]) -> dict[int, int]:
        out: dict[int, int] = {}
        api = self.apis[account]
        for offer, nm in cards:
            d = api.post("/content/v2/get/cards/list",
                         {"settings": {"cursor": {"limit": 10}, "filter": {"textSearch": offer, "withPhoto": -1}}})
            for c in d.get("cards") or []:
                if c["nmID"] == nm:
                    out[nm] = c["imtID"]
        return out

    def move(self, account: str, nm_ids: list[int], target: int | None) -> None:
        body: dict = {"nmIDs": nm_ids}
        if target is not None:
            body["targetIMT"] = target
        self.apis[account].post("/content/v2/cards/moveNm", body, write=True)


def read_env_files(env: str, merge: str = "") -> tuple[dict[str, str], dict[str, str]]:
    p = conf("MARKETPLACE_ENV", env)
    if not p:
        raise SystemExit("не указан .env с ключами: параметр --env или MARKETPLACE_ENV (окружение или .env проекта)")
    m = merge_env_path(merge)
    return read_env(p), (read_env(m) if m else {})
