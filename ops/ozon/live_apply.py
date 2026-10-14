"""Склейки Озона: правила -> план -> (по флагу) применение -> проверка. Всё пишется в папку логов запуска.

    python -m ops.ozon.live_apply --accounts proftex                 # только план (ничего не пишет на Озон)
    python -m ops.ozon.live_apply --accounts proftex --apply         # план + применение + проверка
    python -m ops.ozon.live_apply --accounts proftex --types "Ткань,Покрывало" --apply
    python -m ops.ozon.live_apply --rollback logs/20261009-120000-proftex --apply   # вернуть прежние названия моделей

Ключи берутся из .env (OZON_CLIENT_ID[_MILKY|_TIMELESS], OZON_API_KEY[...], SELSUP_API_TOKEN) и в логи не попадают.
Запускать из корня репозитория marketplace_gluing (нужны пакеты gluing и planner).
Что меняется на Озоне: только атрибут 9048 «Название модели» у карточек из плана. Остальное не трогается.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import json
import logging
import re
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ops import ENV_PATH
from gluing.ozon.features import pick_feature, sku_feature
from gluing.ozon.loader import _type_of, apply_roll, skus_from_rows
from gluing.ozon.model import Design
from gluing.ozon.tree import build
from planner.ids import assign_ids

ROOT = Path(__file__).resolve().parents[2]
ACCOUNTS = {"proftex": "", "milky": "_MILKY", "timeless": "_TIMELESS"}
MODEL_ATTR = 9048                    # «Название модели»
BATCH = 100
log = logging.getLogger("glue")


# ---------- ключи и запросы ----------

def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class Ozon:
    def __init__(self, client_id: str, api_key: str):
        self.h = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    def post(self, path: str, body: dict) -> dict:
        err = None
        for attempt in range(5):
            try:
                req = urllib.request.Request("https://api-seller.ozon.ru" + path, json.dumps(body).encode(), self.h)
                return json.load(urllib.request.urlopen(req, timeout=90))
            except urllib.error.HTTPError as e:
                err = f"HTTP {e.code}: {e.read().decode()[:300]}"
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except Exception as e:                                   # сеть
                err = f"{type(e).__name__}: {e}"
            time.sleep(2 + attempt * 3)
        raise RuntimeError(f"{path}: {err}")


def selsup_rolls(token: str) -> dict[str, int]:
    """Рулоны PTxxxx на складе FBS Селсапа (id 10001), метры."""
    def get(path, params):
        q = urllib.parse.urlencode(params, doseq=True)
        for a in range(4):
            try:
                req = urllib.request.Request("https://api.selsup.ru" + path + "?" + q, headers={"Authorization": token})
                return json.load(urllib.request.urlopen(req, timeout=90))
            except Exception:
                time.sleep(2 + a * 3)
        raise RuntimeError("selsup: " + path)
    rows = [r for r in get("/api/wms/stock/all", {"warehouseId": 10001}) if r.get("quantity")]
    pid = lambda r: ((r.get("sku") or {}).get("product") or {}).get("id") or r.get("skuId")
    ids = list({pid(r) for r in rows if pid(r)})
    info = {}
    for i in range(0, len(ids), 200):
        for x in get("/api/product/find", {"ids": ids[i:i + 200], "limit": 200}).get("rows", []):
            info[x["id"]] = x
    m: collections.Counter = collections.Counter()
    for r in rows:
        art = ((r.get("sku") or {}).get("product") or {}).get("anyArticle") or \
              (((info.get(pid(r)) or {}).get("view") or {}).get("model") or {}).get("article")
        if art and re.fullmatch(r"PT\d+", art):
            m[art] += float(r["quantity"])
    return {a: int(q) for a, q in m.items()}


# ---------- выгрузка кабинета ----------

def fetch_account(oz: Ozon, acct: str, days: int = 28) -> tuple[list[dict], dict[str, dict]]:
    """-> (строки для планировщика, {offer_id: {category_id, type_id, product_id}})."""
    items, last = [], ""
    while True:
        r = oz.post("/v3/product/list", {"filter": {"visibility": "ALL"}, "last_id": last, "limit": 1000})["result"]
        items += r["items"]
        last = r["last_id"]
        if not r["items"] or len(items) >= r["total"]:
            break
    ids = [i["product_id"] for i in items]
    info = []
    for i in range(0, len(ids), 1000):
        info += oz.post("/v3/product/info/list", {"product_id": ids[i:i + 1000]})["items"]
    tname: dict = {}

    def walk(nodes, cat=None):
        for n in nodes:
            if "type_id" in n:
                tname[(cat, n["type_id"])] = n["type_name"]
            walk(n.get("children", []), n.get("description_category_id", cat))
    walk(oz.post("/v1/description-category/tree", {"language": "DEFAULT"})["result"])
    model: dict = {}
    for i in range(0, len(ids), 1000):
        for p in oz.post("/v4/product/info/attributes", {"filter": {"product_id": ids[i:i + 1000], "visibility": "ALL"},
                                                         "limit": 1000})["result"]:
            for a in p.get("attributes", []):
                if a["id"] == MODEL_ATTR and a["values"]:
                    model[p["id"]] = a["values"][0]["value"]
    to = dt.datetime.now(dt.timezone.utc)
    since = to - dt.timedelta(days=days)
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    cnt: collections.Counter = collections.Counter()
    for path, key in (("/v3/posting/fbs/list", "postings"), ("/v2/posting/fbo/list", None)):
        off = 0
        while True:
            r = oz.post(path, {"dir": "ASC", "filter": {"since": fmt(since), "to": fmt(to)}, "limit": 1000, "offset": off})["result"]
            ps = r[key] if key else r
            for p in ps:
                if p.get("status") != "cancelled":
                    for pr in p.get("products", []):
                        cnt[pr["offer_id"]] += pr.get("quantity", 1)
            if len(ps) < 1000:
                break
            off += 1000
    rows, meta = [], {}
    for p in info:
        st = sum(s.get("present", 0) for s in p.get("stocks", {}).get("stocks", []))
        rows.append({"account": acct, "offer_id": p["offer_id"], "title": p["name"], "stock": st,
                     "category": tname.get((p.get("description_category_id"), p.get("type_id")), "?"),
                     "orders28": cnt.get(p["offer_id"], 0), "model_name": model.get(p["id"], ""), "product_id": p["id"]})
        meta[p["offer_id"]] = {"category_id": p.get("description_category_id"), "type_id": p.get("type_id"), "product_id": p["id"]}
    return rows, meta


def types_with_model_attr(oz: Ozon, meta: dict, types_used: set[str], rows: list[dict]) -> set[str]:
    """Типы товара, у категории которых есть атрибут 9048 (иначе Озон молча игнорирует запись)."""
    seen: dict = {}
    ok: set[str] = set()
    for r in rows:
        if r["category"] not in types_used or r["category"] in ok:
            continue
        m = meta[r["offer_id"]]
        k = (m["category_id"], m["type_id"])
        if k not in seen:
            res = oz.post("/v1/description-category/attribute", {"description_category_id": k[0], "type_id": k[1], "language": "DEFAULT"})
            seen[k] = any(a["id"] == MODEL_ATTR for a in res.get("result", []))
        if seen[k]:
            ok.add(r["category"])
    return ok


# ---------- правила ----------

def build_designs_whole(skus, accounts, profile) -> tuple[list[Design], list[str]]:
    """Дизайны. Если у дизайна хотя бы один размер проходит по остатку, берём все его размеры."""
    brands = profile["brands"]
    min_total = profile.get("stock", {}).get("min_total", 2)
    key_of = lambda s, a: (a, brands.get(s.prefix), _type_of(s.category), s.prefix + s.design_no)
    active = {key_of(s, a) for s, a in zip(skus, accounts) if s.stock + s.stock_roll >= min_total}
    ds: dict = {}
    unknown: list[str] = []
    for s, a in zip(skus, accounts):
        if brands.get(s.prefix) is None:
            if s.stock + s.stock_roll >= min_total:
                unknown.append(s.offer_id)
            continue
        key = key_of(s, a)
        if key not in active:
            continue
        ft = sku_feature(key[2], s)
        sub = (key, ft)
        d = ds.get(sub)
        if d is None:
            d = ds[sub] = Design(account=a, brand=key[1], typ=key[2], design=key[3], digits=s.design_no)
        d.skus.append(s)
        d.features[ft] += 1
    # Признак считается по каждому SKU: рюши и обычные размеры одного дизайна идут в разные группы.
    # Часть с самым редким признаком сохраняет прежний uid (липкость), остальные получают суффикс «~признак».
    by_design: dict = {}
    for (key, ft), d in ds.items():
        by_design.setdefault(key, []).append((ft, d))
    out = []
    for key, parts in by_design.items():
        rare = pick_feature(Design(account="", brand="", typ=key[2], design="", digits="",
                                   features=sum((d.features for _, d in parts), collections.Counter())))
        for ft, d in parts:
            d.feature = ft
            if ft != rare:
                d.design = f"{key[3]}~{ft}"
            out.append(d)
    return out, unknown


def plan_account(acct: str, rows: list[dict], rolls: dict[str, int], profile: dict, state: dict):
    skus, accts = skus_from_rows(rows, rolls)
    designs, unknown = build_designs_whole(skus, accts, profile)
    groups, decisions, new_state = build(designs, profile, state)
    live = {r["offer_id"]: r["model_name"] for r in rows}
    ids = assign_ids([(g.key, [s.offer_id for d in g.designs for s in d.skus]) for g in groups],
                     state.get("ids"), live)
    new_state["ids"] = ids
    return groups, ids, live, new_state, unknown


# ---------- применение ----------

def wait_task(oz: Ozon, task_id: int, n: int = 0) -> list[dict]:
    items: list[dict] = []
    for _ in range(40):
        items = oz.post("/v1/product/import/info", {"task_id": task_id}).get("result", {}).get("items", [])
        if items and len(items) >= n and all(x["status"] in ("imported", "failed", "skipped") for x in items):
            return items
        time.sleep(10)
    return items


def read_models(oz: Ozon, offers: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(offers), BATCH):
        for p in oz.post("/v4/product/info/attributes", {"filter": {"offer_id": offers[i:i + BATCH], "visibility": "ALL"}, "limit": BATCH})["result"]:
            out[p["offer_id"]] = {a["id"]: a["values"] for a in p["attributes"]}
    return out


def model_of(attrs: dict) -> str:
    v = attrs.get(MODEL_ATTR)
    return v[0]["value"] if v else ""


def write_models(oz: Ozon, ops: list[dict], run_dir: Path, label: str) -> list[dict]:
    """ops: [{offer_id, from, to}] -> результаты по каждому артикулу. Перед записью перечитывает текущее значение."""
    results: list[dict] = []
    for i in range(0, len(ops), BATCH):
        batch = ops[i:i + BATCH]
        cur = read_models(oz, [o["offer_id"] for o in batch])
        todo = []
        for o in batch:
            now = model_of(cur.get(o["offer_id"], {}))
            if o["offer_id"] not in cur:
                results.append({**o, "status": "not_found", "error": "карточки нет", "task_id": ""})
            elif now != o["from"]:
                results.append({**o, "status": "stale", "error": f"с момента плана модель изменилась: «{now}»", "task_id": ""})
            else:
                todo.append(o)
        if not todo:
            continue
        r = oz.post("/v1/product/attributes/update", {"items": [
            {"offer_id": o["offer_id"], "attributes": [{"complex_id": 0, "id": MODEL_ATTR, "values": [{"value": o["to"]}]}]} for o in todo]})
        tid = r.get("task_id")
        by = {x["offer_id"]: x for x in wait_task(oz, tid, len(todo))} if tid else {}
        for o in todo:
            x = by.get(o["offer_id"], {})
            results.append({**o, "task_id": tid, "status": x.get("status", "no_status"),
                            "error": "; ".join(str(e.get("message") or e) for e in x.get("errors", []))})
        log.info("%s: пакет %d, записано %d, статусы %s", label, i // BATCH + 1, len(todo),
                 dict(collections.Counter(results[-len(todo) + k]["status"] for k in range(len(todo)))))
    return results


def verify(oz: Ozon, results: list[dict], pause: int = 30) -> None:
    sent = [r for r in results if r["status"] in ("imported", "skipped", "no_status")]
    time.sleep(pause)
    now = read_models(oz, [r["offer_id"] for r in sent])
    for r in results:
        if r in sent:
            r["verified"] = "ok" if model_of(now.get(r["offer_id"], {})) == r["to"] else "mismatch"
        else:
            r["verified"] = "not_sent"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


# ---------- запуск ----------

def setup_log(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(run_dir / "run.log", encoding="utf-8"), logging.StreamHandler(sys.stdout)])


def run_account(acct: str, a, env: dict, profile: dict, rolls: dict, stamp: str) -> dict:
    run_dir = Path(a.logs) / f"{stamp}-{acct}"
    setup_log(run_dir)
    log.info("=== кабинет %s | режим: %s | папка: %s", acct, "ПРИМЕНЕНИЕ" if a.apply else "только план", run_dir)
    sfx = ACCOUNTS[acct]
    oz = Ozon(env["OZON_CLIENT_ID" + sfx], env["OZON_API_KEY" + sfx])
    rows, meta = fetch_account(oz, acct)
    log.info("выгружено карточек: %d, с названием модели: %d", len(rows), sum(1 for r in rows if r["model_name"]))
    state_file = Path(a.state) / f"{acct}.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    groups, ids, live, new_state, unknown = plan_account(acct, rows, rolls, profile, state)
    log.info("правила: групп %d (%s), SKU в плане %d, неизвестных брендов %d", len(groups),
             dict(collections.Counter(g.kind for g in groups)), sum(len(d.skus) for g in groups for d in g.designs), len(unknown))
    wanted = {t.strip() for t in a.types.split(",")} if a.types else None
    usable = types_with_model_attr(oz, meta, {g.typ for g in groups}, rows)
    ops, plan_rows, skipped = [], [], collections.Counter()
    for g in groups:
        for d in g.designs:
            for s in d.skus:
                base = {"offer_id": s.offer_id, "product_id": meta[s.offer_id]["product_id"], "type": g.typ,
                        "group_key": g.key, "kind": g.kind, "from": live.get(s.offer_id, ""), "to": ids[g.key]}
                if wanted and g.typ not in wanted:
                    reason = "тип не выбран"
                elif g.typ not in usable:
                    reason = "у категории нет атрибута 9048"
                elif base["from"] == base["to"]:
                    reason = "уже в нужной модели"
                else:
                    reason = ""
                plan_rows.append({**base, "action": reason or "записать"})
                if reason:
                    skipped[reason] += 1
                else:
                    ops.append(base)
    write_csv(run_dir / "plan.csv", plan_rows)
    write_csv(run_dir / "groups.csv", [{"group_key": g.key, "group_id": ids[g.key], "kind": g.kind, "type": g.typ,
                                        "designs": len(g.designs), "skus": sum(len(d.skus) for d in g.designs)} for g in groups])
    summary = {"account": acct, "time": stamp, "applied": a.apply, "groups": len(groups), "ops": len(ops), "skipped": dict(skipped),
               "by_type": dict(collections.Counter(o["type"] for o in ops))}
    log.info("план: записать %d, пропущено %s, по типам %s", len(ops), dict(skipped), summary["by_type"])
    if a.apply and ops:
        if len(ops) > a.max_ops:
            log.error("операций %d больше лимита --max-ops %d: применение остановлено (повысьте лимит осознанно)", len(ops), a.max_ops)
            summary["stopped"] = "max_ops"
        else:
            snap = read_models(oz, [o["offer_id"] for o in ops])
            (run_dir / "snapshot_before.json").write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            results = []
            if a.probe:                                               # сначала один артикул: если не сработал, дальше не идём
                results = write_models(oz, ops[:1], run_dir, "проба")
                verify(oz, results, 20)
                if results[0].get("verified") != "ok":
                    log.error("проба не прошла (%s), остановка", results[0])
                    summary["stopped"] = "probe_failed"
                    ops = []
            rest = [o for o in ops if o["offer_id"] not in {r["offer_id"] for r in results}]
            if rest:
                results += write_models(oz, rest, run_dir, "применение")
                verify(oz, results[len(results) - len(rest):], 30)
            write_csv(run_dir / "results.csv", results)
            summary["results"] = dict(collections.Counter(r["status"] for r in results))
            summary["verified"] = dict(collections.Counter(r.get("verified", "") for r in results))
            bad = [r for r in results if r.get("verified") != "ok"]
            log.info("ИТОГ: %s | проверка: %s", summary["results"], summary["verified"])
            for r in bad[:20]:
                log.warning("не применено: %s (%s → %s) статус=%s проверка=%s %s", r["offer_id"], r["from"], r["to"], r["status"], r.get("verified"), r["error"])
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    if a.apply and not summary.get("stopped"):
        Path(a.state).mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(new_state, ensure_ascii=False), encoding="utf-8")
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h); h.close()
    return summary


def rollback(a, env: dict) -> None:
    run_dir = Path(a.rollback)
    acct = run_dir.name.split("-", 2)[-1]
    setup_log(run_dir.parent / (run_dir.name + "-rollback"))
    snap = json.loads((run_dir / "snapshot_before.json").read_text(encoding="utf-8"))
    res = list(csv.DictReader(open(run_dir / "results.csv", encoding="utf-8-sig")))
    ops = [{"offer_id": r["offer_id"], "from": r["to"], "to": model_of({int(k): v for k, v in snap[r["offer_id"]].items()}),
            "type": r.get("type", "")} for r in res if r.get("verified") == "ok"]
    empty = [o for o in ops if not o["to"]]
    ops = [o for o in ops if o["to"]]
    log.info("откат: вернуть %d, без прежней модели (очистить вручную): %d", len(ops), len(empty))
    if not a.apply:
        log.info("без --apply ничего не пишу"); return
    sfx = ACCOUNTS[acct]
    oz = Ozon(env["OZON_CLIENT_ID" + sfx], env["OZON_API_KEY" + sfx])
    results = write_models(oz, ops, run_dir, "откат")
    verify(oz, results)
    log.info("ИТОГ отката: %s", dict(collections.Counter(r.get("verified") for r in results)))
    write_csv(run_dir.parent / (run_dir.name + "-rollback") / "results.csv", results)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--accounts", default="proftex", help="через запятую: proftex,milky,timeless")
    ap.add_argument("--types", default="", help="только эти типы товара, через запятую (по умолчанию все)")
    ap.add_argument("--apply", action="store_true", help="записать на Озон (без флага — только план)")
    ap.add_argument("--max-ops", type=int, default=1500, help="предохранитель: больше операций за запуск не применять")
    ap.add_argument("--no-probe", dest="probe", action="store_false", help="не делать пробную запись одного артикула")
    ap.add_argument("--no-rolls", action="store_true", help="не подтягивать рулоны из Селсапа (ткани получат меньше остатка)")
    ap.add_argument("--env", default=str(ENV_PATH))
    ap.add_argument("--profile", default=str(ROOT / "profiles" / "ozon.toml"))
    ap.add_argument("--logs", default=str(ROOT / "logs"))
    ap.add_argument("--state", default=str(ROOT / "state"))
    ap.add_argument("--rollback", default="", help="папка прошлого запуска, чьи изменения нужно откатить")
    a = ap.parse_args()
    env = read_env(Path(a.env))
    if a.rollback:
        rollback(a, env)
        return
    profile = tomllib.loads(Path(a.profile).read_text(encoding="utf-8"))
    rolls: dict[str, int] = {}
    if not a.no_rolls:
        if "SELSUP_API_TOKEN" not in env:
            sys.exit("нет SELSUP_API_TOKEN в .env (или запустите с --no-rolls)")
        rolls = selsup_rolls(env["SELSUP_API_TOKEN"])
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = []
    for acct in [x.strip() for x in a.accounts.split(",") if x.strip()]:
        if acct not in ACCOUNTS:
            sys.exit(f"неизвестный кабинет {acct}: {list(ACCOUNTS)}")
        try:
            out.append(run_account(acct, a, env, profile, rolls, stamp))
        except Exception:
            logging.getLogger().exception("кабинет %s: сбой", acct)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
