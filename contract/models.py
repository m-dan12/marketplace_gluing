from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 1

Kind = Literal["glue", "small_glue", "single_design", "unglued"]


class Member(BaseModel):
    offer_id: str
    stock: int = 0
    orders_28d: int = 0
    platform_ids: dict[str, int | str | None] = Field(default_factory=dict)   # product_id, sku / nm_id


class Design(BaseModel):
    design: str
    feature: str = ""
    members: list[Member]


class Group(BaseModel):
    group_id: str
    key: str
    account: str
    brand: str
    type: str
    branch: str
    part: int = 1
    kind: Kind
    target: dict = Field(default_factory=dict)            # озон: {"model_name": ...}, wb: {"imt_id": ...}
    designs: list[Design]
    reasons: list[str] = Field(default_factory=list)

    @property
    def n_designs(self) -> int:
        return len(self.designs)

    @property
    def n_skus(self) -> int:
        return sum(len(d.members) for d in self.designs)


class Decision(BaseModel):
    node: str
    level: str
    name: str
    n: int
    result: str


class Check(BaseModel):
    hard_violations: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RunInfo(BaseModel):
    run_id: str
    platform: Literal["ozon", "wb"]
    created_at: str
    engine_version: str
    profile_name: str
    profile_hash: str
    inputs: dict = Field(default_factory=dict)
    mode: Literal["shadow", "manual", "auto"] = "shadow"


class Plan(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run: RunInfo
    groups: list[Group]
    unglued: list[dict] = Field(default_factory=list)
    checks: Check = Field(default_factory=Check)
    decisions: list[Decision] = Field(default_factory=list)
    state: dict = Field(default_factory=dict)             # гистерезис и назначение для завтрашнего запуска

    @model_validator(mode="after")
    def _consistent(self):
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"неизвестная версия схемы {self.schema_version}")
        ids = [g.group_id for g in self.groups]
        if len(ids) != len(set(ids)):
            raise ValueError("group_id повторяется")
        seen: dict[str, str] = {}
        for g in self.groups:
            for d in g.designs:
                for m in d.members:
                    if m.offer_id in seen:
                        raise ValueError(f"артикул {m.offer_id} в двух группах: {seen[m.offer_id]} и {g.group_id}")
                    seen[m.offer_id] = g.group_id
        return self

    def dumps(self) -> str:
        return self.model_dump_json(indent=1)

    @classmethod
    def loads(cls, text: str) -> "Plan":
        return cls.model_validate_json(text)


def plan_json_schema() -> str:
    return json.dumps(Plan.model_json_schema(), ensure_ascii=False, indent=1)
