"""Применяющий скрипт WB, часть 2: перенос карточек (moveNm), сверка, откат.

Каждый шаг проверяет живое состояние: карточка уже в целевой склейке — пропуск (идемпотентность), карточка лежит не там, где ждали
по плану, — пропуск как устаревшая. Перенос в склейку не превышает жёсткий лимит: если место занято карточками, которые
сами уходят по плану, переносы идут по мере освобождения; цикл «A ждёт B, B ждёт A» разрывается отделением одной карточки.
После каждой записи состояние перечитывается и сверяется; каждая операция пишется в журнал (gluing.op), по нему строится откат.
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Protocol

from applier.executor import Journal
from applier.ops import Op
from applier.wb_ops import HARD_MAX, NEW

BATCH = 30


class WbPort(Protocol):
    def imts(self, account: str, cards: list[tuple[str, int]]) -> dict[int, int]:
        """{nm_id: текущий imtID} для пар (артикул, nm_id); карточки, которых нет на WB, в ответ не попадают."""

    def move(self, account: str, nm_ids: list[int], target: int | None) -> None:
        """moveNm: присоединить к склейке target; без target — отделить (карточка получает новую склейку)."""


@dataclass
class WbSettings:
    probe: bool = True
    max_ops: int = 1500
    verify_pause: int = 7
    split_polls: int = 12
    split_delay: int = 15
    hard_max: int = HARD_MAX
    sleep: Callable = time.sleep


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:200]}"


class _Account:
    """Применение операций одного кабинета."""

    def __init__(self, acc: str, rows: list[dict], nm: dict[tuple[str, str], int], port: WbPort, journal: Journal,
                 st: WbSettings, sizes: dict[int, int], probe: bool):
        self.acc, self.pending, self.nm, self.port, self.j, self.st = acc, list(rows), nm, port, journal, st
        self.size = dict(sizes)
        self.exp = {r["op_id"]: int(r["from_value"]) for r in rows}      # где карточка должна лежать сейчас
        self.resolved: dict[str, int] = {}
        self.res: Counter = Counter()
        self.probe = probe
        self.stopped = ""

    def n(self, r: dict) -> int:
        return self.nm[(r["account"], r["offer_id"])]

    def target(self, r: dict) -> int | None:
        to = r["to_value"]
        return self.resolved.get(to[len(NEW):]) if to.startswith(NEW) else int(to)

    def finish(self, r: dict, status: str, error: str = "") -> None:
        self.j.update(r["op_id"], status, error)
        self.res[status] += 1
        self.pending.remove(r)

    def move_size(self, frm: int, to: int) -> None:
        self.size[frm] = self.size.get(frm, 1) - 1
        self.size[to] = self.size.get(to, 0) + 1

    def refresh(self) -> None:
        live = self.port.imts(self.acc, [(r["offer_id"], self.n(r)) for r in self.pending])
        for r in list(self.pending):
            cur = live.get(self.n(r))
            tgt = self.target(r)
            if cur is None:
                self.finish(r, "failed", "карточки нет на WB")
            elif tgt is not None and cur == tgt:
                self.finish(r, "done")                                   # уже на месте
            elif cur != self.exp[r["op_id"]]:
                self.finish(r, "skipped", f"склейка изменилась с момента плана: {cur}")

    def split_one(self, r: dict) -> int | None:
        """Отделяет одну карточку и возвращает её новый imtID."""
        old, n = self.exp[r["op_id"]], self.n(r)
        self.port.move(self.acc, [n], None)
        for _ in range(self.st.split_polls):
            self.st.sleep(self.st.split_delay)
            cur = self.port.imts(self.acc, [(r["offer_id"], n)]).get(n)
            if cur is not None and cur != old:
                self.move_size(old, cur)
                self.exp[r["op_id"]] = cur
                return cur
        return None

    def resolve_new(self) -> bool:
        progress = False
        gids = {r["to_value"][len(NEW):] for r in self.pending if r["to_value"].startswith(NEW)} - set(self.resolved)
        for gid in sorted(gids):
            rows = [r for r in self.pending if r["to_value"] == NEW + gid]
            seed = rows[0]
            try:
                new = self.split_one(seed)
            except Exception as e:
                for r in rows:
                    self.finish(r, "failed", f"отделить карточку не удалось: {_err(e)}")
                progress = True
                continue
            if new is None:
                for r in rows:
                    self.finish(r, "failed", "разъединение не подтвердилось")
                progress = True
                continue
            self.resolved[gid] = new
            self.finish(seed, "done")
            progress = True
        return progress

    def move_batches(self) -> bool:
        progress = False
        by_target: dict[int, list[dict]] = defaultdict(list)
        for r in self.pending:
            t = self.target(r)
            if t is not None:
                by_target[t].append(r)
        for t, rows in by_target.items():
            free = self.st.hard_max - self.size.get(t, 0)
            if free <= 0:
                continue
            take = rows[:min(BATCH, free, 1 if self.probe else BATCH)]
            ids = [self.n(r) for r in take]
            try:
                self.port.move(self.acc, ids, t)
                self.st.sleep(self.st.verify_pause)
                after = self.port.imts(self.acc, [(r["offer_id"], self.n(r)) for r in take])
            except Exception as e:
                for r in take:
                    self.finish(r, "failed", _err(e))
                progress = True
                if self.probe:
                    self.stopped = "probe_failed"
                    return True
                continue
            ok = 0
            for r in take:
                got = after.get(self.n(r))
                if got == t:
                    self.move_size(self.exp[r["op_id"]], t)
                    self.finish(r, "done")
                    ok += 1
                else:
                    self.finish(r, "failed", f"сверка: на WB склейка {got}, ожидалась {t}")
            progress = True
            if self.probe:
                if not ok:
                    self.stopped = "probe_failed"
                    return True
                self.probe = False
        return progress

    def break_cycle(self) -> bool:
        """Все переносы ждут места: отделяем карточку, которая уходит из склейки, чтобы освободить место."""
        for r in self.pending:
            t = self.target(r)
            if t is None or self.size.get(t, 0) < self.st.hard_max:
                continue
            for x in self.pending:                                     # карточка, которая сама уходит из полной целевой склейки
                if self.exp[x["op_id"]] == t:
                    try:
                        return self.split_one(x) is not None
                    except Exception:
                        return False
        return False

    def run(self) -> None:
        for _ in range(4 * len(self.pending) + 10):
            if not self.pending or self.stopped:
                break
            self.refresh()
            if not self.pending:
                break
            progress = self.resolve_new()
            progress = self.move_batches() or progress
            if self.stopped:
                break
            if not progress and not self.break_cycle():
                break
        if self.pending and not self.stopped:
            self.stopped = "stuck"
        for r in list(self.pending):
            self.finish(r, "skipped", "остановлено: " + (self.stopped or "прервано"))


def execute_wb(run_id: str, ops: list[Op], ports: dict[str, WbPort], journal: Journal, st: WbSettings | None = None,
               sizes: dict[str, dict[int, int]] | None = None) -> dict:
    """Применяет операции move_nm. sizes — {кабинет: {imtID: число карточек}} из dry-run."""
    st = st or WbSettings()
    if len(ops) > st.max_ops:
        return {"stopped": "max_ops", "ops": len(ops), "limit": st.max_ops}
    ids = journal.add(run_id, ops)
    wanted = set(ids)
    rows = [r for r in journal.rows(run_id) if r["op_id"] in wanted]
    nm = {(o.account, o.offer_id): int(o.product_id) for o in ops}
    summary: Counter = Counter()
    stopped = ""
    probe = st.probe
    by_acc: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_acc[r["account"]].append(r)
    for acc, rs in by_acc.items():
        a = _Account(acc, rs, nm, ports[acc], journal, st, (sizes or {}).get(acc, {}), probe)
        a.run()
        probe = probe and a.probe
        summary.update(a.res)
        stopped = stopped or a.stopped
        if a.stopped == "probe_failed":
            for acc2, rs2 in by_acc.items():
                if acc2 != acc:
                    for r in rs2:
                        journal.update(r["op_id"], "skipped", "остановлено: пробный перенос не прошёл")
                        summary["skipped"] += 1
            break
    out = {"result": dict(summary)}
    if stopped:
        out["stopped"] = stopped
    return out


def rollback_wb(run_id: str, ports: dict[str, WbPort], journal: Journal, nm: dict[tuple[str, str], int],
                st: WbSettings | None = None) -> dict:
    """Возвращает карточки в прежние склейки (nm — {(кабинет, артикул): nm_id} из плана). Склейка, которой больше нет, пересоздаётся: одна карточка отделяется, остальные к ней."""
    st = st or WbSettings()
    done = journal.rows(run_id, "done")
    summary: Counter = Counter()
    by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in done:
        if r["from_value"].isdigit():
            by[(r["account"], r["from_value"])].append(r)
    for (acc, old), rows in by.items():
        port = ports[acc]
        nms = [(r["offer_id"], nm[(acc, r["offer_id"])]) for r in rows]
        ids = [n for _, n in nms]
        try:
            try:
                port.move(acc, ids, int(old))
            except Exception:                                          # склейки уже нет: создаём заново
                port.move(acc, ids[:1], None)
                st.sleep(st.split_delay)
                new = port.imts(acc, nms[:1]).get(ids[0])
                if new is None:
                    raise RuntimeError("новая склейка не появилась")
                if ids[1:]:
                    port.move(acc, ids[1:], new)
            st.sleep(st.verify_pause)
            after = port.imts(acc, nms)
            same = len({after.get(n) for n in ids}) == 1 and None not in {after.get(n) for n in ids}
        except Exception as e:
            for r in rows:
                journal.update(r["op_id"], "done", f"откат не удался: {_err(e)}")
                summary["failed"] += 1
            continue
        for r in rows:
            if same:
                journal.update(r["op_id"], "reverted")
                summary["reverted"] += 1
            else:
                journal.update(r["op_id"], "done", "откат не подтвердился")
                summary["failed"] += 1
    return {"result": dict(summary)}
