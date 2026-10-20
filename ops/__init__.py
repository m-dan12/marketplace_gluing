"""Рабочие скрипты: выгрузка данных и запись на маркетплейсы. Запускать из корня: python -m ops.wb.wb_plan_all ..."""
import os
from pathlib import Path

ENV_PATH = Path(os.environ.get("MARKETPLACE_ENV", "Z:/marketplace_db_sync/.env"))        # ключи кабинетов WB/Selsup
MERGE_ENV_PATH = Path(os.environ.get("MARKETPLACE_MERGE_ENV", "Z:/.env"))                 # токены только для склеек WB (moveNm)
