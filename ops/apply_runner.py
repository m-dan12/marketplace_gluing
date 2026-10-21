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


WB_TASK_NAME = "GluingWbApplyRun"


def command(run_id: str, keys_env: str, max_ops: int, only: str = "", script: str = "apply_ozon.py", extra: str = "") -> str:
    py = sys.executable
    flt = f" --only {only}" if only else ""
    return (f'set PYTHONIOENCODING=utf-8& "{py}" {script} --run {run_id} --execute --confirm {run_id} --env {keys_env}{extra}'
            f" --max-ops {max_ops}{flt} >> logs\\apply_{run_id}.log 2>&1")


def start(run_id: str, keys_env: str, max_ops: int, only: str = "", script: str = "apply_ozon.py", extra: str = "",
          task: str = TASK_NAME) -> str:
    """Регистрирует и запускает задание применения. keys_env — путь к .env с ключами площадки."""
    (ROOT / "logs").mkdir(exist_ok=True)
    cmd = command(run_id, keys_env, max_ops, only, script, extra).replace("'", "''")
    return _ps(SCRIPT.replace("@CMD@", cmd).replace("@WORK@", str(ROOT)).replace("@NAME@", task))


def start_wb(run_id: str, keys_env: str, merge_env: str, max_ops: int) -> str:
    return start(run_id, keys_env, max_ops, script="apply_wb.py", extra=f" --merge-env {merge_env}" if merge_env else "", task=WB_TASK_NAME)


def is_running(task: str = TASK_NAME) -> bool:
    out = _ps(f"(Get-ScheduledTask -TaskName '{task}' -ErrorAction SilentlyContinue).State")
    return out.strip() == "Running"
