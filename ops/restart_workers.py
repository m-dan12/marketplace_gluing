"""Настоящий перезапуск постоянных подписчиков шины после выкладки кода.

    python ops/restart_workers.py            # оба сервера
    python ops/restart_workers.py proftex    # или laya

Почему не schtasks /end: он не убивает дочерние процессы (venv-лаунчер, python-воркер, ssh-туннель), и воркер продолжает работать
на старом коде, а schtasks /run игнорируется, пока задание «выполняется». Скрипт сам завершает процессы по командной строке
(bus_worker.py и, на laya, туннель -L 15432), затем запускает задания и печатает время старта новых процессов."""
from __future__ import annotations

import subprocess
import sys

CFG = "C:/MainFolder/sshdata/config"
HOSTS = {
    "proftex": ("proftex-server", ["GluingWorkerAttrs", "GluingWorkerApply", "GluingWorkerNotify", "GluingWorkerWb"], False),
    "laya": ("laya-server", ["GluingWorkerPlan"], True),
}


def run(host: str, cmd: str, timeout: int = 120) -> str:
    r = subprocess.run(["ssh", "-F", CFG, host, cmd], capture_output=True, timeout=timeout)
    out = (r.stdout + r.stderr).decode("utf-8", "replace")
    return "\n".join(l for l in out.splitlines() if l.strip() and not any(s in l for s in ("WARNING", "quantum", "store now", "openssh")))


def restart(name: str) -> None:
    host, tasks, tunnel = HOSTS[name]
    pat = "bus_worker.py" + ("|-L 15432" if tunnel else "")
    kill = ("powershell -NoProfile -Command \"$n = 0; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match '" + pat +
            "' -and $_.Name -match 'python|ssh' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $n++ }; "
            "'остановлено процессов: ' + $n\"")
    print(f"[{name}]", run(host, kill))
    start = " & ".join(f"schtasks /run /tn {t}" for t in tasks)
    run(host, "ping -n 4 127.0.0.1 >nul & " + start)
    show = ("powershell -NoProfile -Command \"Start-Sleep 6; Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'bus_worker.py' -and "
            "$_.Name -match 'python' } | ForEach-Object { $_.CreationDate.ToString('dd.MM HH:mm:ss') + ' ' + "
            "($_.CommandLine -replace '.*bus_worker.py','') }; 'сейчас ' + (Get-Date -Format 'dd.MM HH:mm:ss')\"")
    print(run(host, show))


if __name__ == "__main__":
    for n in (sys.argv[1:] or list(HOSTS)):
        restart(n)
