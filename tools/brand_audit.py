"""Аудит брендов Озона (только чтение): сводка «бренд по плану × бренд на Озоне» и справочник значений атрибута 85.

    python -m tools.brand_audit                       # сводка по последнему плану (БД)
    python -m tools.brand_audit --dictionary          # + справочник значений бренда из API Озона (читающие вызовы)

Бренд по плану берётся из префикса артикула (profiles/ozon.toml [brands]); бренд на Озоне — атрибут 85 из gluing.sku_attr."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict

BRAND = "85"
SEARCH_TERMS = ["Сказка", "Анна Мария", "Нет бренда", "Мечта", "Milky Garden", "Котики", "Timeless"]


def crosstab(rows: list[dict]) -> list[dict]:
    """rows: {account, plan_brand, live_brand, offer_id, stock, title}. -> строки сводки по (кабинет, бренд плана, бренд Озона),
    у каждой: число артикулов, число с остатком, примеры, признак главного значения (самое частое для этого бренда плана)."""
    agg: dict[tuple, dict] = {}
    for r in rows:
        k = (r["account"], r["plan_brand"], r["live_brand"])
        a = agg.setdefault(k, {"account": k[0], "plan_brand": k[1], "live_brand": k[2], "n": 0, "in_stock": 0, "samples": []})
        a["n"] += 1
        a["in_stock"] += 1 if r["stock"] > 0 else 0
        if len(a["samples"]) < 3:
            a["samples"].append((r["offer_id"], r["title"]))
    main: dict[tuple, str] = {}
    best: dict[tuple, int] = defaultdict(int)
    for (acc, pb, lb), a in agg.items():
        if a["n"] > best[(acc, pb)]:
            best[(acc, pb)], main[(acc, pb)] = a["n"], lb
    out = []
    for (acc, pb, lb), a in agg.items():
        a["main"] = main[(acc, pb)] == lb
        out.append(a)
    return sorted(out, key=lambda a: (a["account"], a["plan_brand"], not a["main"], -a["n"]))


def render(table: list[dict]) -> str:
    lines = [f"{'кабинет':<14}{'бренд по плану':<20}{'бренд на Озоне':<18}{'артикулов':>10}{'с остатком':>11}  примеры"]
    for a in table:
        mark = "" if a["main"] else "  <-- расхождение"
        ex = "; ".join(f"{o} «{t[:38]}»" for o, t in a["samples"][:2])
        lines.append(f"{a['account']:<14}{a['plan_brand']:<20}{a['live_brand']:<18}{a['n']:>10}{a['in_stock']:>11}  {ex}{mark}")
    bad = [a for a in table if not a["main"]]
    lines += ["", f"Артикулов всего {sum(a['n'] for a in table)}, расходятся с основным брендом ветки: {sum(a['n'] for a in bad)} "
              f"(в {len(bad)} сочетаниях)"]
    return "\n".join(lines)


def load_rows(conn, plan) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("select account, offer_id, attrs, name from gluing.sku_attr")
        live = {(a, o): (at, nm) for a, o, at, nm in cur.fetchall()}
    conn.rollback()
    rows = []
    for g in plan.groups:
        for d in g.designs:
            for m in d.members:
                at, nm = live.get((g.account, m.offer_id), ({}, ""))
                rows.append({"account": g.account, "plan_brand": g.brand, "offer_id": m.offer_id, "stock": m.stock,
                             "live_brand": (at.get(BRAND) or [""])[0] or "(пусто)", "title": nm or ""})
    return rows


def dictionary(conn, env_file: str) -> None:
    from applier.ozon_client import api_for, read_env
    env = read_env(env_file)
    with conn.cursor() as cur:
        cur.execute("select account, category_id, type_id, count(*) from gluing.sku_attr group by 1, 2, 3 order by 1, 4 desc")
        cats: dict[str, tuple] = {}
        for acc, c, t, _ in cur.fetchall():
            cats.setdefault(acc, (c, t))
    conn.rollback()
    for acc, (cid, tid) in cats.items():
        api = api_for(acc, env)
        print(f"\n== {acc}: категория {cid}, тип {tid}")
        attrs = api.post("/v1/description-category/attribute", {"description_category_id": cid, "type_id": tid, "language": "DEFAULT"})
        meta = next((a for a in attrs.get("result", []) if a["id"] == int(BRAND)), None)
        if meta is None:
            print("   атрибута 85 в категории нет")
            continue
        keep = ("name", "type", "dictionary_id", "is_required", "is_aspect", "is_collection", "category_dependent",
                "attribute_complex_id", "max_value_count", "description")
        print("   атрибут 85:", {k: (str(meta.get(k))[:90]) for k in keep if k in meta})
        for term in SEARCH_TERMS:
            res = api.post("/v1/description-category/attribute/values/search",
                           {"attribute_id": int(BRAND), "description_category_id": cid, "type_id": tid, "value": term, "limit": 8})
            vals = res.get("result") or []
            print(f"   поиск «{term}»: " + ("; ".join(f"{v['id']}={v['value']!r}" for v in vals) or "ничего"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="")
    ap.add_argument("--dictionary", action="store_true")
    a = ap.parse_args()
    import psycopg
    from ops.bus_worker import conf
    from plan_ozon import database_url
    from planner.store import PgStore
    with psycopg.connect(database_url()) as conn:
        store = PgStore(conn)
        plan = store.load(a.run) if a.run else store.last("ozon")
        print(f"план {plan.run.run_id}\n")
        print(render(crosstab(load_rows(conn, plan))))
        if a.dictionary:
            dictionary(conn, conf("MARKETPLACE_ENV"))


if __name__ == "__main__":
    main()
