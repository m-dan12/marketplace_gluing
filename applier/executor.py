"""Применяющий скрипт Озона, часть 2: запись, сверка, откат. Логика перенесена из проверенных рабочих скриптов
(ops/ozon/live_apply.py, color_write.py) и дополнена журналом операций.

Порядок: пробная запись одного артикула → пакеты по 100 (перед записью перечитывается текущее значение: если оно уже
не равно «было» из плана, артикул пропускается как устаревший) → ожидание статуса задачи Озона → пауза и перечитывание
значения (сверка). Каждая операция пишется в журнал со статусом и «было → стало», по нему строится откат.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Protocol

from applier.ops import BRAND_ATTR, COLOR_NAME_ATTR, MODEL_NAME_ATTR, Op

BATCH = 100
DONE_STATUSES = ("imported", "failed", "skipped")


class Api(Protocol):
    def post(self, path: str, body: dict) -> dict: ...


class Journal(Protocol):
    def add(self, run_id: str, ops: list[Op]) -> list[int]: ...
    def update(self, op_id: int, status: str, error: str = "") -> None: ...
    def rows(self, run_id: str, status: str | None = None) -> list[dict]: ...


class MemoryJournal:
    def __init__(self):
        self.data: dict[int, dict] = {}

    def add(self, run_id, ops):
        ids = []
        for o in ops:
            i = len(self.data) + 1
            self.data[i] = {"op_id": i, "run_id": run_id, "account": o.account, "offer_id": o.offer_id,
                            "op_type": o.op_type, "attr_id": o.attr_id, "from_value": o.from_value,
                            "to_value": o.to_value, "from_id": o.from_id, "to_id": o.to_id, "status": "pending", "error": ""}
            ids.append(i)
        return ids

    def update(self, op_id, status, error=""):
        self.data[op_id].update(status=status, error=error)

    def rows(self, run_id, status=None):
        return [dict(r) for r in self.data.values() if r["run_id"] == run_id and (status is None or r["status"] == status)]


class PgJournal:
    """Журнал в gluing.op. attr_id выводится из op_type, поэтому отдельной колонки не нужно."""

    ATTR = {"set_model_name": MODEL_NAME_ATTR, "set_color_name": COLOR_NAME_ATTR, "unglue_model": MODEL_NAME_ATTR,
            "set_brand": BRAND_ATTR}

    def __init__(self, conn):
        self.conn = conn

    def add(self, run_id, ops):
        ids = []
        with self.conn.transaction(), self.conn.cursor() as cur:
            for o in ops:
                cur.execute("insert into gluing.op(run_id,account,offer_id,op_type,from_value,to_value,from_id,to_id,attr_id)"
                            " values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning op_id",
                            (run_id, o.account, o.offer_id, o.op_type, o.from_value, o.to_value, o.from_id, o.to_id, o.attr_id))
                ids.append(cur.fetchone()[0])
        return ids

    def update(self, op_id, status, error=""):
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("update gluing.op set status=%s, error=%s, attempts=attempts+1,"
                        " applied_at=case when %s in ('done','reverted') then now() else applied_at end where op_id=%s",
                        (status, error, status, op_id))

    def rows(self, run_id, status=None):
        q, args = ("select op_id,run_id,account,offer_id,op_type,from_value,to_value,status,coalesce(error,''),from_id,to_id,attr_id"
                   " from gluing.op where run_id=%s"), [run_id]
        if status:
            q += " and status=%s"
            args.append(status)
        with self.conn.cursor() as cur:
            cur.execute(q + " order by op_id", args)
            cols = ["op_id", "run_id", "account", "offer_id", "op_type", "from_value", "to_value", "status", "error", "from_id", "to_id", "attr_id"]
            out = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in out:
            r["attr_id"] = r["attr_id"] or self.ATTR.get(r["op_type"], 0)
        return out


# ---------- обмен с Озоном ----------

def read_values(api: Api, offers: list[str], attr_id: int) -> dict[str, str]:
    """{offer_id: значение атрибута} (первое значение; у нет атрибута — пустая строка). Нет карточки — ключа нет."""
    out: dict[str, str] = {}
    for i in range(0, len(offers), BATCH):
        d = api.post("/v4/product/info/attributes",
                     {"filter": {"offer_id": offers[i:i + BATCH], "visibility": "ALL"}, "limit": BATCH})
        for p in d.get("result") or []:
            vals = next((a["values"] for a in p.get("attributes") or [] if a["id"] == attr_id), [])
            out[p["offer_id"]] = vals[0]["value"] if vals else ""
    return out


def wait_task(api: Api, task_id: int, n: int, sleep: Callable = time.sleep, polls: int = 40, delay: int = 8) -> dict[str, dict]:
    items: list[dict] = []
    for _ in range(polls):
        sleep(delay)
        items = api.post("/v1/product/import/info", {"task_id": task_id}).get("result", {}).get("items", [])
        if len(items) >= n and all(x["status"] in DONE_STATUSES for x in items):
            break
    return {x["offer_id"]: x for x in items}


@dataclass
class Settings:
    probe: bool = True
    max_ops: int = 1500
    verify_pause: int = 30
    recheck: int = 120          # повторная сверка всех применённых значений через столько секунд (0 — выключена)
    sleep: Callable = time.sleep


def _value(o: dict) -> dict:
    """Значение атрибута для записи: у справочных атрибутов (бренд) обязательно dictionary_value_id, у текстовых только value."""
    return {"dictionary_value_id": o["to_id"], "value": o["to_value"]} if o.get("to_id") else {"value": o["to_value"]}


def _write_batch(api: Api, ops: list[dict], attr_id: int, st: Settings) -> dict[str, dict]:
    d = api.post("/v1/product/attributes/update", {"items": [
        {"offer_id": o["offer_id"], "attributes": [{"complex_id": 0, "id": attr_id, "values": [_value(o)]}]}
        for o in ops]})
    tid = d.get("task_id")
    return wait_task(api, tid, len(ops), st.sleep) if tid else {}


def _apply_group(api: Api, rows: list[dict], attr_id: int, journal: Journal, st: Settings, stale_check: bool) -> Counter:
    """rows — записи журнала одного кабинета и типа операции. Возвращает счётчик итоговых статусов."""
    res: Counter = Counter()
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        todo = []
        if stale_check:
            now = read_values(api, [r["offer_id"] for r in batch], attr_id)
            for r in batch:
                if r["offer_id"] not in now:
                    journal.update(r["op_id"], "failed", "карточки нет на Озоне")
                    res["failed"] += 1
                elif now[r["offer_id"]] != r["from_value"]:
                    journal.update(r["op_id"], "skipped", f"значение изменилось с момента плана: «{now[r['offer_id']]}»")
                    res["skipped"] += 1
                else:
                    todo.append(r)
        else:
            todo = batch
        if not todo:
            continue
        by = _write_batch(api, todo, attr_id, st)
        st.sleep(st.verify_pause)
        after = read_values(api, [r["offer_id"] for r in todo], attr_id)
        for r in todo:
            x = by.get(r["offer_id"], {})
            errs = "; ".join(str(e.get("message") or e) for e in x.get("errors", []))
            if x.get("status") == "failed":
                journal.update(r["op_id"], "failed", errs or "Озон отклонил")
                res["failed"] += 1
            elif after.get(r["offer_id"]) == r["to_value"]:
                journal.update(r["op_id"], "done")
                res["done"] += 1
            else:
                journal.update(r["op_id"], "failed", f"сверка: на Озоне «{after.get(r['offer_id'], '')}», ожидалось «{r['to_value']}»")
                res["failed"] += 1
    return res


def _recheck(run_id: str, op_ids: set[int], apis: dict[str, Api], journal: Journal, st: Settings) -> Counter:
    """Озон иногда принимает запись, показывает новое значение и потом откатывает его. Через st.recheck секунд перечитываем всё
    применённое: что не закрепилось, помечается failed (и вычитается из done)."""
    out: Counter = Counter()
    if not st.recheck:
        return out
    st.sleep(st.recheck)
    by: dict[tuple, list[dict]] = {}
    for r in journal.rows(run_id, "done"):
        if r["op_id"] in op_ids:
            by.setdefault((r["account"], r["attr_id"]), []).append(r)
    for (acc, aid), rs in by.items():
        now = read_values(apis[acc], [r["offer_id"] for r in rs], aid)
        for r in rs:
            if now.get(r["offer_id"]) != r["to_value"]:
                journal.update(r["op_id"], "failed", f"не закрепилось: через {st.recheck} с на Озоне «{now.get(r['offer_id'], '')}»")
                out["done"] -= 1
                out["failed"] += 1
                out["слетело после повторной сверки"] += 1
    return out


def execute(run_id: str, ops: list[Op], apis: dict[str, Api], journal: Journal, st: Settings | None = None) -> dict:
    """Применяет операции. Сначала бренд (склеивать можно только карточки одного бренда), затем правки цвета (чтобы убрать
    близнецов), потом названия моделей, потом снятие с модели."""
    st = st or Settings()
    if len(ops) > st.max_ops:
        return {"stopped": "max_ops", "ops": len(ops), "limit": st.max_ops}
    ids = journal.add(run_id, ops)
    rows = [r for r in journal.rows(run_id) if r["op_id"] in set(ids)]
    order = {"set_brand": 0, "set_color_name": 1, "set_model_name": 2, "unglue_model": 3}
    rows.sort(key=lambda r: (order[r["op_type"]], r["account"], r["op_id"]))
    summary: Counter = Counter()
    if st.probe and rows:                                              # проба: пока одна операция не применится, дальше не идём
        probed = 0
        for r in list(rows[:5]):
            c = _apply_group(apis[r["account"]], [r], r["attr_id"], journal, st, True)
            summary.update(c)
            rows.remove(r)
            if c.get("failed"):
                for x in rows:
                    journal.update(x["op_id"], "skipped", "остановлено: пробная запись не прошла")
                return {"stopped": "probe_failed", "result": dict(summary), "error": journal.rows(run_id, "failed")[:1]}
            if c.get("done"):
                probed = 1
                break                                                  # устаревшая проба (skipped) не считается: берём следующую
        if not probed:
            for x in rows:
                journal.update(x["op_id"], "skipped", "остановлено: пробная запись не удалась")
            return {"stopped": "probe_failed", "result": dict(summary)}
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["op_type"], r["account"], r["attr_id"]), []).append(r)
    for (typ, acc, aid), g in groups.items():
        summary.update(_apply_group(apis[acc], g, aid, journal, st, True))
    summary.update(_recheck(run_id, set(ids), apis, journal, st))
    return {"result": dict(summary)}


def rollback(run_id: str, apis: dict[str, Api], journal: Journal, st: Settings | None = None) -> dict:
    """Возвращает прежние значения у применённых операций. Операции, где «было» пусто, откатить нельзя (очистить вручную)."""
    st = st or Settings()
    done = journal.rows(run_id, "done")
    def revertible(r: dict) -> bool:               # пустое «было» вернуть нельзя; бренд без id справочника тоже
        return r["from_value"] != "" and not (r["op_type"] == "set_brand" and not r.get("from_id"))

    manual = [r for r in done if not revertible(r)]
    todo = [{**r, "from_value": r["to_value"], "to_value": r["from_value"], "from_id": r.get("to_id"), "to_id": r.get("from_id")}
            for r in reversed(done) if revertible(r)]
    summary: Counter = Counter()
    groups: dict[tuple, list[dict]] = {}
    for r in todo:
        groups.setdefault((r["op_type"], r["account"], r["attr_id"]), []).append(r)
    for (typ, acc, aid), g in groups.items():
        class _Rev:                                    # журнал отката обновляет исходные операции
            def update(self, op_id, status, error=""):
                if status == "done":
                    journal.update(op_id, "reverted")
                else:                                  # откат не удался: исходная операция остаётся применённой
                    journal.update(op_id, "done", f"откат не удался: {error}")
        summary.update(_apply_group(apis[acc], g, aid, _Rev(), st, True))
    return {"result": dict(summary), "очистить вручную (прежнее значение пустое)": [r["offer_id"] for r in manual]}
