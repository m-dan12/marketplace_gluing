"""Перечитывает склейки карточек WB (imtID, цвет) в gluing.wb_card. Только чтение с WB.

    python -m tools.wb_refresh [--accounts skazka milky_garden timeless] [--env <.env с ключами>]"""
from __future__ import annotations

import argparse

import psycopg

from applier.wb_client import ACCOUNT_ENV, api_for, read_env_files
from plan_ozon import database_url
from planner.wb_live import refresh_wb_cards


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", nargs="*", default=list(ACCOUNT_ENV), choices=list(ACCOUNT_ENV))
    ap.add_argument("--env", default="")
    a = ap.parse_args()
    env, merge = read_env_files(a.env)
    with psycopg.connect(database_url(), autocommit=True) as conn:
        for acc in a.accounts:
            print(refresh_wb_cards(conn, acc, api_for(acc, env, merge)), flush=True)


if __name__ == "__main__":
    main()
