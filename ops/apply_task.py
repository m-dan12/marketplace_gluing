"""Запуск боевого применения плана Озона как задания Планировщика на proftex-server (переживает обрыв SSH).

    python ops/apply_task.py --run 2026-10-20-ozon-7 [--only set_brand,set_model_name,unglue_model] [--approve-by danil]

Что делает: (по --approve-by) согласует план, регистрирует разовое задание GluingApplyRun (вход S4U, без расписания), запускает его.
Задание вызывает apply_ozon.py --execute --confirm <run_id>; журнал: gluing.op, текстовый лог: logs\\apply_<run_id>.log.
Запись возможна только по явной команде владельца: скрипт сам ничего не запускает по расписанию."""
from __future__ import annotations

import argparse
import subprocess
import sys

CFG = "C:/MainFolder/sshdata/config"
PP = r"C:\Proftex-Server\marketplace_gluing"
KEYS = r"C:\Proftex-Server\marketplace_db_sync\.env"

TASK = r"""
$ErrorActionPreference = 'Stop'
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "@CMD@"') -WorkingDirectory '@WORK@'
$principal = New-ScheduledTaskPrincipal -UserId ($env:COMPUTERNAME + '\' + $env:USERNAME) -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'GluingApplyRun' -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'GluingApplyRun'
'started: ' + (Get-ScheduledTask -TaskName 'GluingApplyRun').State
"""


def ssh(cmd: str, data: bytes | None = None, timeout: int = 300) -> str:
    r = subprocess.run(["ssh", "-F", CFG, "proftex-server", cmd], input=data, capture_output=True, timeout=timeout)
    t = (r.stdout + r.stderr).decode("utf-8", "replace")
    if r.returncode:
        sys.exit(f"ошибка rc={r.returncode}: {t[-600:]}")
    return "\n".join(l for l in t.splitlines() if not any(s in l for s in ("WARNING", "post-quantum", "store now", "openssh.com")))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--max-ops", type=int, default=12000)
    ap.add_argument("--approve-by", default="")
    a = ap.parse_args()
    py = rf"{PP}\.venv\Scripts\python.exe"
    if a.approve_by:
        print(ssh(rf"cd /d {PP} & set PYTHONIOENCODING=utf-8& {py} apply_ozon.py --run {a.run} --approve --by {a.approve_by}"))
    only = f" --only {a.only}" if a.only else ""
    cmd = (rf"set PYTHONIOENCODING=utf-8& {py} apply_ozon.py --run {a.run} --execute --confirm {a.run} --env {KEYS}"
           rf" --max-ops {a.max_ops}{only} >> logs\apply_{a.run}.log 2>&1")
    ps = TASK.replace("@CMD@", cmd).replace("@WORK@", PP)
    print(ssh("powershell -NoProfile -Command -", ps.encode("utf-8")))


if __name__ == "__main__":
    main()
