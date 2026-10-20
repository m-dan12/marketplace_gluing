"""Запуск боевого применения плана как задания Планировщика ЛОКАЛЬНО на сервере, где крутится подписчик apply.

Применение может идти часами, поэтому оно не выполняется внутри подписчика шины и не привязано к SSH-сессии: подписчик согласует план
и стартует разовое задание GluingApplyRun (вход S4U, без расписания), которое вызывает apply_ozon.py --execute --confirm <run_id>.
Итог задание публикует в шину событием gluing.ozon.applied (см. apply_ozon.py)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_NAME = "GluingApplyRun"

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "@CMD@"') -WorkingDirectory '@WORK@'
$principal = New-ScheduledTaskPrincipal -UserId ($env:COMPUTERNAME + '\' + $env:USERNAME) -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName '@NAME@' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName '@NAME@'
'started'
"""


def _ps(script: str, timeout: int = 120) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", "-"], input=script.encode("utf-8"),
                       capture_output=True, timeout=timeout)
    out = (r.stdout + r.stderr).decode("utf-8", "replace").strip()
    if r.returncode:
        raise RuntimeError(f"powershell rc={r.returncode}: {out[-400:]}")
    return out


def command(run_id: str, keys_env: str, max_ops: int, only: str = "") -> str:
    py = sys.executable
    flt = f" --only {only}" if only else ""
    return (f'set PYTHONIOENCODING=utf-8& "{py}" apply_ozon.py --run {run_id} --execute --confirm {run_id} --env {keys_env}'
            f" --max-ops {max_ops}{flt} >> logs\\apply_{run_id}.log 2>&1")


def start(run_id: str, keys_env: str, max_ops: int, only: str = "") -> str:
    """Регистрирует и запускает задание применения. keys_env — путь к .env с ключами Озона."""
    (ROOT / "logs").mkdir(exist_ok=True)
    cmd = command(run_id, keys_env, max_ops, only).replace("'", "''")
    return _ps(SCRIPT.replace("@CMD@", cmd).replace("@WORK@", str(ROOT)).replace("@NAME@", TASK_NAME))


def is_running() -> bool:
    out = _ps(f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue).State")
    return out.strip() == "Running"
