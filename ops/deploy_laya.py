"""Выкладка кода на laya-server без git: git archive -> zip -> ssh -> распаковка.

    python ops/deploy_laya.py                 # проект склеек + marketplace_bus (если лежит рядом) и установка в venv
    python ops/deploy_laya.py --no-install    # только код

Каталоги на laya-server: C:\\Users\\User_2\\marketplace\\gluing, ...\\bus, venv ...\\venv. .env не трогается."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SSH = ["ssh", "-F", "C:/MainFolder/sshdata/config", "laya-server"]
BASE = r"C:\Users\User_2\marketplace"
HERE = Path(__file__).resolve().parent.parent
BUS = HERE.parent / "marketplace_bus"


def remote(cmd: str, data: bytes | None = None) -> str:
    r = subprocess.run(SSH + [cmd], input=data, capture_output=True)
    out = (r.stdout + r.stderr).decode("cp866", "replace")
    skip = ("WARNING", "post-quantum", "store now", "openssh.com")
    out = "\n".join(l for l in out.splitlines() if not any(s in l for s in skip))
    if r.returncode:
        raise SystemExit(f"ошибка ({r.returncode}) на laya-server:\n{out[-1500:]}")
    return out


# Приёмный скрипт кладётся на laya-server файлом, затем запускается с zip на stdin (вложенные кавычки cmd/PowerShell ненадёжны).
RECEIVER = r"""param($name)
$base = 'C:\Users\User_2\marketplace'
$d = Join-Path $base $name
$z = Join-Path $env:TEMP ($name + '.zip')
$ms = New-Object IO.MemoryStream
[Console]::OpenStandardInput().CopyTo($ms)
[IO.File]::WriteAllBytes($z, $ms.ToArray())
New-Item -ItemType Directory -Force $d | Out-Null
Get-ChildItem $d -Force | Where-Object { $_.Name -ne '.env' } | Remove-Item -Recurse -Force
Expand-Archive -Force $z $d
Remove-Item $z
$name + ': ' + @(Get-ChildItem $d -Recurse -File).Count + ' files'
"""


def ship(repo: Path, name: str) -> None:
    zipped = subprocess.run(["git", "-C", str(repo), "archive", "--format=zip", "HEAD"], capture_output=True, check=True).stdout
    remote(r'powershell -NoProfile -Command "[Console]::In.ReadToEnd() | Set-Content -Encoding UTF8 $env:TEMP\recv_deploy.ps1"',
           RECEIVER.encode("utf-8"))
    print(remote(rf"powershell -NoProfile -ExecutionPolicy Bypass -File %TEMP%\recv_deploy.ps1 {name}", zipped).strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-install", action="store_true")
    a = ap.parse_args()
    ship(HERE, "gluing")
    if BUS.exists():
        ship(BUS, "bus")
    if not a.no_install:
        py = rf"{BASE}\venv\Scripts\python.exe"
        remote(f"if not exist {py} (python -m venv {BASE}\\venv)")
        print(remote(f"{py} -m pip install -q --disable-pip-version-check -r {BASE}\\gluing\\requirements.txt {BASE}\\bus"))
        print(remote(f"{py} -c \"import ortools, psycopg, busclient; print('venv ok', ortools.__version__)\""))


if __name__ == "__main__":
    sys.exit(main())
