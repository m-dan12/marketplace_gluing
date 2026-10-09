"""Хранилище планов. Интерфейс один, реализации две: память (тесты) и Postgres (схема gluing)."""
from __future__ import annotations

import json
from typing import Protocol

from contract import Check, Decision, Design, Group, Member, Plan, RunInfo

KEEP = ("applied", "partial", "approved", "applying")      # запуски, от которых можно брать вчерашнее состояние


class PlanStore(Protocol):
    def save_draft(self, plan: Plan) -> None: ...
    def load(self, run_id: str) -> Plan | None: ...
    def last(self, platform: str, statuses: tuple[str, ...] | None = None) -> Plan | None: ...
    def status(self, run_id: str) -> str | None: ...
    def set_status(self, run_id: str, status: str, by: str | None = None) -> None: ...


class MemoryStore:
    def __init__(self):
        self.plans: dict[str, str] = {}
        self.statuses: dict[str, str] = {}
        self.order: list[str] = []

    def save_draft(self, plan: Plan) -> None:
        rid = plan.run.run_id
        if rid in self.plans:
            raise ValueError(f"запуск {rid} уже есть")
        for old in self.order:                                       # новый черновик заменяет несогласованный
            if self.statuses[old] == "draft" and self.load(old).run.platform == plan.run.platform:
                self.statuses[old] = "expired"
        self.plans[rid], self.statuses[rid] = plan.dumps(), "draft"
        self.order.append(rid)

    def load(self, run_id):
        return Plan.loads(self.plans[run_id]) if run_id in self.plans else None

    def last(self, platform, statuses=None):
        for rid in reversed(self.order):
            p = self.load(rid)
            if p.run.platform == platform and (statuses is None or self.statuses[rid] in statuses):
                return p
        return None

    def status(self, run_id):
        return self.statuses.get(run_id)

    def set_status(self, run_id, status, by=None):
        self.statuses[run_id] = status


class PgStore:
    """psycopg-соединение к роли gluing_app. Запись плана — одна транзакция."""

    def __init__(self, conn):
        self.conn = conn

    def save_draft(self, plan: Plan) -> None:
        r = plan.run
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("update gluing.run set status='expired' where platform=%s and status='draft'", (r.platform,))
            cur.execute(
                "insert into gluing.run(run_id,platform,created_at,engine_version,profile_hash,profile_name,inputs,mode,"
                "status,state,checks,unglued) values (%s,%s,%s,%s,%s,%s,%s,%s,'draft',%s,%s,%s)",
                (r.run_id, r.platform, r.created_at, r.engine_version, r.profile_hash, r.profile_name,
                 json.dumps(r.inputs), r.mode, json.dumps(plan.state), plan.checks.model_dump_json(),
                 json.dumps(plan.unglued)))
            for g in plan.groups:
                cur.execute(
                    "insert into gluing.glue_group(run_id,group_id,key,account,brand,typ,branch,part,kind,n_designs,n_skus,"
                    "target,reasons) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (r.run_id, g.group_id, g.key, g.account, g.brand, g.type, g.branch, g.part, g.kind, g.n_designs,
                     g.n_skus, json.dumps(g.target), json.dumps(g.reasons, ensure_ascii=False)))
                cur.executemany(
                    "insert into gluing.member(run_id,group_id,account,offer_id,design,feature,stock,orders_28d,platform_ids)"
                    " values (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    [(r.run_id, g.group_id, g.account, m.offer_id, d.design, d.feature, m.stock, m.orders_28d,
                      json.dumps(m.platform_ids)) for d in g.designs for m in d.members])
            cur.executemany(
                "insert into gluing.decision(run_id,node,level,name,n,result) values (%s,%s,%s,%s,%s,%s)",
                [(r.run_id, x.node, x.level, x.name, x.n, x.result) for x in plan.decisions])

    def load(self, run_id: str) -> Plan | None:
        with self.conn.cursor() as cur:
            cur.execute("select platform,created_at,engine_version,profile_name,profile_hash,inputs,mode,state,checks,"
                        "unglued from gluing.run where run_id=%s", (run_id,))
            row = cur.fetchone()
            if not row:
                return None
            plat, created, ev, pn, ph, inputs, mode, state, checks, unglued = row
            cur.execute("select group_id,key,account,brand,typ,branch,part,kind,target,reasons from gluing.glue_group"
                        " where run_id=%s order by key", (run_id,))
            gl = cur.fetchall()
            cur.execute("select group_id,design,feature,offer_id,stock,orders_28d,platform_ids from gluing.member"
                        " where run_id=%s order by design,offer_id", (run_id,))
            ms = cur.fetchall()
            cur.execute("select node,level,name,n,result from gluing.decision where run_id=%s order by id", (run_id,))
            dec = cur.fetchall()
        by: dict[str, dict[str, Design]] = {}
        for gid, des, feat, offer, stock, o28, pids in ms:
            d = by.setdefault(gid, {}).setdefault(des, Design(design=des, feature=feat or "", members=[]))
            d.members.append(Member(offer_id=offer, stock=stock or 0, orders_28d=o28 or 0, platform_ids=pids or {}))
        groups = [Group(group_id=gid, key=k, account=a, brand=b, type=t, branch=br, part=p, kind=kd, target=tg,
                        reasons=rs, designs=list(by.get(gid, {}).values()))
                  for gid, k, a, b, t, br, p, kd, tg, rs in gl]
        return Plan(run=RunInfo(run_id=run_id, platform=plat, created_at=created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                engine_version=ev, profile_name=pn, profile_hash=ph, inputs=inputs, mode=mode),
                    groups=groups, unglued=unglued, checks=Check(**checks), state=state,
                    decisions=[Decision(node=n, level=lv, name=nm, n=c, result=res) for n, lv, nm, c, res in dec])

    def last(self, platform: str, statuses: tuple[str, ...] | None = None) -> Plan | None:
        q, args = "select run_id from gluing.run where platform=%s", [platform]
        if statuses:
            q += " and status = any(%s)"
            args.append(list(statuses))
        with self.conn.cursor() as cur:
            cur.execute(q + " order by created_at desc, run_id desc limit 1", args)
            row = cur.fetchone()
        return self.load(row[0]) if row else None

    def status(self, run_id: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute("select status from gluing.run where run_id=%s", (run_id,))
            row = cur.fetchone()
        return row[0] if row else None

    def set_status(self, run_id: str, status: str, by: str | None = None) -> None:
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.execute("update gluing.run set status=%s, approved_by=coalesce(%s, approved_by),"
                        " approved_at=case when %s='approved' then now() else approved_at end where run_id=%s",
                        (status, by, status, run_id))
            cur.execute("insert into gluing.audit(user_name,action,run_id,details) values (%s,%s,%s,'{}')",
                        (by or "system", f"status:{status}", run_id))
