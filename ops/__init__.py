"""Рабочие скрипты: выгрузка данных, запись на маркетплейсы, проверки. Запускать из корня: python -m ops.ozon.live_apply ..."""
import os
from pathlib import Path

ENV_PATH = Path(os.environ.get("MARKETPLACE_ENV", "Z:/marketplace_db_sync/.env"))        # ключи кабинетов Озона/WB/Selsup
MERGE_ENV_PATH = Path(os.environ.get("MARKETPLACE_MERGE_ENV", "Z:/.env"))                 # токены только для склеек WB (moveNm)
