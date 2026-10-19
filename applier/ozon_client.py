"""Клиент API Озона для применяющего скрипта. Ключи кабинетов берутся из .env (путь задаётся параметром или MARKETPLACE_ENV)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

BASE = "https://api-seller.ozon.ru"
ACCOUNT_ENV = {"skazka": ("OZON_CLIENT_ID", "OZON_API_KEY"),
               "timeless": ("OZON_CLIENT_ID_TIMELESS", "OZON_API_KEY_TIMELESS"),
               "milky_garden": ("OZON_CLIENT_ID_MILKY", "OZON_API_KEY_MILKY")}
RETRY = (429, 500, 502, 503, 504)


def env_path(explicit: str = "") -> Path:
    p = explicit or os.environ.get("MARKETPLACE_ENV", "")
    if not p:
        raise SystemExit("не указан .env с ключами: параметр --env или переменная MARKETPLACE_ENV")
    return Path(p)


def read_env(path: Path | str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.split("#", 1)[0].strip().strip('"').strip("'")
    return env


class OzonApi:
    """POST с повторами на сетевые ошибки и 429/5xx. Тело и ключи не логируются."""

    def __init__(self, client_id: str, api_key: str, *, client: httpx.Client | None = None, sleep=time.sleep):
        self._h = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
        self._client = client or httpx.Client(timeout=90.0)
        self._sleep = sleep

    def post(self, path: str, body: dict) -> dict:
        err = ""
        for attempt in range(5):
            try:
                r = self._client.post(BASE + path, headers=self._h, json=body)
                if r.status_code in RETRY:
                    err = f"HTTP {r.status_code}"
                elif r.status_code >= 400:
                    raise RuntimeError(f"{path}: HTTP {r.status_code}: {r.text[:300]}")
                else:
                    return r.json()
            except httpx.HTTPError as e:
                err = f"{type(e).__name__}"
            self._sleep(2 + attempt * 3)
        raise RuntimeError(f"{path}: {err}")


def api_for(account: str, env: dict[str, str]) -> OzonApi:
    if account not in ACCOUNT_ENV:
        raise SystemExit(f"неизвестный кабинет {account}: {list(ACCOUNT_ENV)}")
    cid, key = ACCOUNT_ENV[account]
    if cid not in env or key not in env:
        raise SystemExit(f"в .env нет ключей кабинета {account}")
    return OzonApi(env[cid], env[key])
