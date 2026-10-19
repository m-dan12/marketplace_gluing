"""Проверка, что CP-SAT вообще работает на этой машине.

На части процессоров нативная библиотека ortools падает при первом же Solve (access violation, процесс умирает молча).
Поэтому перед использованием решателя один раз за процесс запускаем крошечную модель в отдельном процессе:
если она не прошла, решатель считается недоступным, а код возвращается к жадной раскладке.
"""
from __future__ import annotations

import subprocess
import sys

_CODE = (
    "from ortools.sat.python import cp_model\n"
    "m = cp_model.CpModel(); x = [m.NewBoolVar(str(i)) for i in range(6)]\n"
    "m.Add(sum(x) >= 2); m.Minimize(sum((i + 1) * x[i] for i in range(6)))\n"
    "s = cp_model.CpSolver(); s.parameters.num_workers = 1\n"
    "print('probe-ok' if s.Solve(m) in (cp_model.OPTIMAL, cp_model.FEASIBLE) else 'probe-bad')\n"
)
_cache: dict[str, object] = {}


def works(timeout: float = 90.0) -> bool:
    if "ok" not in _cache:
        try:
            r = subprocess.run([sys.executable, "-c", _CODE], capture_output=True, text=True, timeout=timeout)
            ok = r.returncode == 0 and "probe-ok" in r.stdout
            _cache["why"] = "" if ok else f"пробный запуск не прошёл (код {r.returncode}); решатель отключён, используется жадная раскладка"
        except Exception as e:                              # таймаут или нет интерпретатора
            ok, _cache["why"] = False, f"пробный запуск не удался ({type(e).__name__}); решатель отключён"
        _cache["ok"] = ok
        if not ok:
            print("CP-SAT недоступен:", _cache["why"], file=sys.stderr)
    return bool(_cache["ok"])


def reason() -> str:
    works()
    return str(_cache.get("why", ""))


def reset() -> None:
    _cache.clear()
