"""Регистрация заданий Планировщика ночной цепочки (вход S4U, без пароля).

    python ops/register_tasks.py        # повторный запуск обновляет задания (Force)

Постоянные подписчики (--loop) — Gluing Worker*: запускаются при старте системы и «сторожем» раз в 10 минут. Пока процесс жив,
повторный запуск игнорируется (MultipleInstances IgnoreNew); если он упал или вышел, сторож поднимет его в течение 10 минут.
Шлюз GluingSyncGate — периодическое задание в утреннем окне, пока db_sync сам не публикует sync.ozon.done."""
from __future__ import annotations

import subprocess
import sys

CFG = "C:/MainFolder/sshdata/config"

COMMON = r"""
$ErrorActionPreference = 'Stop'
$name = '@NAME@'
$work = '@WORK@'
$cmd  = '@CMD@'
New-Item -ItemType Directory -Force (Join-Path $work 'logs') | Out-Null
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $cmd + '"') -WorkingDirectory $work
$principal = New-ScheduledTaskPrincipal -UserId ($env:COMPUTERNAME + '\' + $env:USERNAME) -LogonType S4U -RunLevel Limited
"""

REGISTER = r"""
Register-ScheduledTask -TaskName $name -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
foreach ($old in @(@OLD@)) { if (Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $old -Confirm:$false; "removed old task " + $old } }
$t = Get-ScheduledTask -TaskName $name
"{0}: {1}, user={2}, next={3}" -f $name, $t.State, $t.Principal.UserId, (Get-ScheduledTaskInfo $t).NextRunTime
"""

WORKER = COMMON + r"""
$t1 = New-ScheduledTaskTrigger -AtStartup
$t2 = New-ScheduledTaskTrigger -Daily -At 00:00
$t2.Repetition = (New-ScheduledTaskTrigger -Once -At 00:00 -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration (New-TimeSpan -Days 1)).Repetition
$triggers = @($t1, $t2)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
""" + REGISTER

PERIODIC = COMMON + r"""
$start = [datetime]::Today.Add([timespan]'@START@')
$t = New-ScheduledTaskTrigger -Daily -At $start
$t.Repetition = (New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes @MIN@) -RepetitionDuration (New-TimeSpan -Hours @HOURS@)).Repetition
$triggers = @($t)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
""" + REGISTER


def register(host, template, name, work, cmd, old=(), start="05:10", minutes=10, hours=6):
    ps = (template.replace("@NAME@", name).replace("@WORK@", work).replace("@CMD@", cmd.replace("'", "''"))
          .replace("@START@", start).replace("@MIN@", str(minutes)).replace("@HOURS@", str(hours))
          .replace("@OLD@", ",".join(f"'{o}'" for o in old)))
    r = subprocess.run(["ssh", "-F", CFG, host, "powershell -NoProfile -Command -"], input=ps.encode("utf-8"),
                       capture_output=True, timeout=120)
    t = (r.stdout + r.stderr).decode("utf-8", "replace")
    print("\n".join(l for l in t.splitlines() if not any(s in l for s in ("WARNING", "post-quantum", "store now", "openssh.com"))).strip())
    if r.returncode:
        sys.exit(f"{host}/{name}: rc={r.returncode}")


PP = r"C:\Proftex-Server\marketplace_gluing"
PY = r".venv\Scripts\python.exe"
register("proftex-server", PERIODIC, "GluingSyncGate", PP, rf"{PY} ops\sync_gate.py >> logs\gate.log 2>&1")
register("proftex-server", WORKER, "GluingWorkerAttrs", PP, rf"{PY} ops\bus_worker.py attrs --loop >> logs\worker_attrs.log 2>&1",
         old=("GluingBusProftex",))
register("proftex-server", WORKER, "GluingWorkerApply", PP, rf"{PY} ops\bus_worker.py apply --loop >> logs\worker_apply.log 2>&1")
register("proftex-server", WORKER, "GluingWorkerNotify", PP, rf"{PY} ops\bus_worker.py notify --loop >> logs\worker_notify.log 2>&1")
register("proftex-server", PERIODIC, "GluingHealth", PP, rf"{PY} ops\health_check.py >> logs\health.log 2>&1",
         start="00:00", minutes=10, hours=23)

LP = r"C:\Users\User_2\marketplace\gluing"
LPY = r"C:\Users\User_2\marketplace\venv\Scripts\python.exe"
register("laya-server", WORKER, "GluingWorkerPlan", LP,
         rf"{LPY} ops\laya_run.py -- {LPY} ops\bus_worker.py plan --loop >> logs\worker_plan.log 2>&1", old=("GluingBusPlan",))
