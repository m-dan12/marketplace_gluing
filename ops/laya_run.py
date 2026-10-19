"""Запуск команды через SSH-туннель к Postgres на proftex-server (для laya-server, где БД нет).

    python ops/laya_run.py -- python plan_ozon.py --dry

Туннель поднимается на время команды и закрывается всегда (и при ошибке). Настройки читаются из .env в корне проекта:
    TUNNEL_TARGET       пользователь@адрес proftex-server, например Proftex1@100.104.20.51
    TUNNEL_KEY          путь к закрытому ключу туннеля (на сервере разрешён только проброс на 127.0.0.1:5432)
    TUNNEL_LOCAL_PORT   локальный порт (по умолчанию 15432)
    TUNNEL_REMOTE_PORT  порт Postgres на proftex-server (по умолчанию 5432)
Строки подключения в .env должны указывать на 127.0.0.1:<TUNNEL_LOCAL_PORT>."""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def open_tunnel(env: dict[str, str]):
    target, key = env.get("TUNNEL_TARGET"), env.get("TUNNEL_KEY")
    if not target or not key:
        raise SystemExit("в .env нужны TUNNEL_TARGET и TUNNEL_KEY")
    local, remote = int(env.get("TUNNEL_LOCAL_PORT", 15432)), int(env.get("TUNNEL_REMOTE_PORT", 5432))
    if port_open(local):
        raise SystemExit(f"порт {local} уже занят (старый туннель?) — закройте его и повторите")
    err = tempfile.TemporaryFile()
    cmd = ["ssh", "-N", "-n", "-i", key, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
           "-o", "ConnectTimeout=15", "-o", "ServerAliveInterval=30", "-o", "StrictHostKeyChecking=accept-new",
           "-L", f"{local}:127.0.0.1:{remote}", target]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err, creationflags=flags)
    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            err.seek(0)
            raise SystemExit("туннель не поднялся: " + err.read().decode("utf-8", "replace").strip()[-400:])
        if port_open(local):
            return proc
        time.sleep(0.3)
    proc.kill()
    raise SystemExit("туннель не поднялся за 30 секунд")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=str(ROOT / ".env"))
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        raise SystemExit("укажите команду после --")
    env = read_env(Path(a.env))
    proc = open_tunnel(env)
    try:
        return subprocess.run(cmd, cwd=ROOT, env={**os.environ, **env}).returncode
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
