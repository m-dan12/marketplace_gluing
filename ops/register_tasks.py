"""Регистрация заданий Планировщика ночной цепочки (S4U, без пароля): шлюз и подписчики на proftex-server, расчёт на laya-server.

    python ops/register_tasks.py        # повторный запуск обновляет задания (Force)
"""
import subprocess
import sys

CFG = "C:/MainFolder/sshdata/config"

TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$name = '@NAME@'
$work = '@WORK@'
$cmd  = '@CMD@'
New-Item -ItemType Directory -Force (Join-Path $work 'logs') | Out-Null
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $cmd + '"') -WorkingDirectory $work
$start = [datetime]::Today.Add([timespan]'@START@')
$trg = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes @MIN@) -RepetitionDuration (New-TimeSpan -Hours @HOURS@)
$trg2 = New-ScheduledTaskTrigger -Daily -At $start
$trg2.Repetition = $trg.Repetition
$principal = New-ScheduledTaskPrincipal -UserId ($env:COMPUTERNAME + '\' + $env:USERNAME) -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trg2 -Principal $principal -Settings $settings -Force | Out-Null
$t = Get-ScheduledTask -TaskName $name
$i = Get-ScheduledTaskInfo $t
"{0}: {1}, user={2}, next={3}" -f $name, $t.State, $t.Principal.UserId, $i.NextRunTime
"""


def register(host, name, work, cmd, start, minutes, hours):
    ps = (TEMPLATE.replace("@NAME@", name).replace("@WORK@", work).replace("@CMD@", cmd.replace("'", "''"))
          .replace("@START@", start).replace("@MIN@", str(minutes)).replace("@HOURS@", str(hours)))
    r = subprocess.run(["ssh", "-F", CFG, host, "powershell -NoProfile -Command -"], input=ps.encode("utf-8"),
                       capture_output=True, timeout=120)
    t = (r.stdout + r.stderr).decode("utf-8", "replace")
    print("\n".join(l for l in t.splitlines() if not any(s in l for s in ("WARNING", "post-quantum", "store now", "openssh.com"))).strip())
    if r.returncode:
        sys.exit(f"{host}/{name}: rc={r.returncode}")


PP = r"C:\Proftex-Server\marketplace_gluing"
PY = r".venv\Scripts\python.exe"
register("proftex-server", "GluingSyncGate", PP,
         rf"{PY} ops\sync_gate.py >> logs\gate.log 2>&1", "05:10", 10, 6)
register("proftex-server", "GluingBusProftex", PP,
         rf"{PY} ops\bus_worker.py attrs >> logs\bus.log 2>&1 & {PY} ops\bus_worker.py apply >> logs\bus.log 2>&1", "00:00", 5, 23)

LP = r"C:\Users\User_2\marketplace\gluing"
LPY = r"C:\Users\User_2\marketplace\venv\Scripts\python.exe"
register("laya-server", "GluingBusPlan", LP,
         rf"{LPY} ops\laya_run.py -- {LPY} ops\bus_worker.py plan >> logs\bus.log 2>&1", "00:00", 5, 23)
