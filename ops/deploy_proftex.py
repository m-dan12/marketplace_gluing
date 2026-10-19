"""Обновление на proftex-server: git pull проекта склеек (ff-only), пакет шины из zip (scp), установка в .venv проекта.

    python ops/deploy_proftex.py

Только scp и простые команды: данные через stdin ssh на Windows зависают. Pull идёт по read-only deploy-ключу из профиля Danil
(под Proftex1 алиас github-gluing не виден), поэтому core.sshCommand указывает на его конфиг."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

CFG = "C:/MainFolder/sshdata/config"
PROJ = r"C:\Proftex-Server\marketplace_gluing"
BUSDIR = r"C:\Proftex-Server\marketplace_bus"
PY = PROJ + r"\.venv\Scripts\python.exe"
BUS = Path(__file__).resolve().parent.parent.parent / "marketplace_bus"


def ssh(cmd: str, timeout: int = 300) -> None:
    try:
        r = subprocess.run(["ssh", "-F", CFG, "proftex-server", cmd], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f"таймаут на proftex-server: {cmd[:100]}")
    t = (r.stdout + r.stderr).decode("cp866", "replace")
    t = "\n".join(l for l in t.splitlines() if not any(s in l for s in ("WARNING", "post-quantum", "store now", "openssh.com")))
    print(t.strip()[-1000:])
    if r.returncode:
        sys.exit(f"ошибка rc={r.returncode}")


def main() -> None:
    ssh(rf'cd /d {PROJ} & git -c core.sshCommand="ssh -F C:/Users/Danil/.ssh/config" pull --ff-only origin main 2>&1')
    z = subprocess.run(["git", "-C", str(BUS), "archive", "--format=zip", "HEAD"], capture_output=True, check=True).stdout
    local = Path(tempfile.gettempdir()) / "deploy_bus_proftex.zip"
    local.write_bytes(z)
    try:
        r = subprocess.run(["scp", "-q", "-F", CFG, str(local), "proftex-server:deploy_bus.zip"], capture_output=True, timeout=180)
    finally:
        local.unlink(missing_ok=True)
    if r.returncode:
        sys.exit("scp: " + r.stderr.decode("cp866", "replace")[-300:])
    ssh(rf"if exist {BUSDIR} rmdir /s /q {BUSDIR} & powershell -NoProfile -Command Expand-Archive -Force -Path %USERPROFILE%\deploy_bus.zip "
        rf"-DestinationPath {BUSDIR} & del %USERPROFILE%\deploy_bus.zip & dir /b {BUSDIR}")
    ssh(rf'{PY} -m pip install -q --disable-pip-version-check {BUSDIR} 2>&1 & {PY} -m pip show busclient | findstr Version', timeout=600)


if __name__ == "__main__":
    main()
