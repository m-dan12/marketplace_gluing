"""Применение плана WB (moveNm). Режимы: --probe <imtID|новая|...> одна группа; --all все группы. Логи в logs/wb-<stamp>/."""
from ops import ENV_PATH, MERGE_ENV_PATH
from ops.common import read_env
import csv, json, time, sys, urllib.request, urllib.error, collections, datetime as dt, argparse
from pathlib import Path

env = read_env(ENV_PATH); import os
ACCT = os.environ.get("WB_ACCT", "proftex"); SUF = "" if ACCT == "proftex" else "_" + ACCT
SFX = {"proftex": "", "milky": "_MILKY", "timeless": "_TIMELESS"}[ACCT]; MT = {"proftex": "SK", "milky": "MG", "timeless": "TL"}[ACCT]
CARDS = f"out/wb_cards{SUF}.json"
KEY = env["WB_API_KEY" + SFX]; MERGE = read_env(MERGE_ENV_PATH)["WB_MERGE_TOKEN_" + MT]   # токен только для склеек (moveNm), чтение — обычным ключом
ap = argparse.ArgumentParser(); ap.add_argument("--probe"); ap.add_argument("--all", action="store_true"); ap.add_argument("--apply", action="store_true")
ap.add_argument("--plan", default=None); ap.add_argument("--skip-new", action="store_true"); a = ap.parse_args()
stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S"); D = Path("logs") / f"wb-{ACCT}-{stamp}"; D.mkdir(parents=True, exist_ok=True)
log = open(D / "run.log", "w", encoding="utf-8")
def L(*x):
    s = " ".join(str(i) for i in x); print(s, flush=True); log.write(s + "\n"); log.flush()
def call(path, body):
    for t in range(12):
        req = urllib.request.Request("https://content-api.wildberries.ru" + path, data=json.dumps(body).encode(), headers={"Authorization": MERGE if "moveNm" in path else KEY, "Content-Type": "application/json"})
        try: return json.load(urllib.request.urlopen(req, timeout=120))
        except urllib.error.HTTPError as e:
            txt = e.read().decode()[:300]
            if e.code == 429: time.sleep(20 * (t + 1)); continue
            raise RuntimeError(f"{path} HTTP {e.code}: {txt}")
def imt_of(nms):
    out = {}
    cards = json.load(open(CARDS, encoding="utf-8"))
    byvc = {c["nmID"]: c["vendorCode"] for c in cards}
    for nm in nms:
        r = call("/content/v2/get/cards/list", {"settings": {"cursor": {"limit": 10}, "filter": {"textSearch": byvc[nm], "withPhoto": -1}}})
        for c in r["cards"]:
            if c["nmID"] == nm: out[nm] = c["imtID"]
    return out
plan = list(csv.DictReader(open(a.plan or f"wb_plan_all{SUF}.csv", encoding="utf-8-sig")))
json.dump({r["nmID"]: r["сейчас_imtID"] for r in plan}, open(D / "snapshot_before.json", "w"))
groups = collections.defaultdict(list)
for r in plan: groups[r["цель"]].append(r)
todo = [g for g, m in groups.items() if any(r["действие"] == "перенести" for r in m)]
if a.probe: todo = [a.probe]
todo = [g for g in todo if not (g.startswith("новая") and len(groups[g]) == 1)]
if a.skip_new: todo = [g for g in todo if not g.startswith("новая")]
L("групп к применению:", len(todo), "| режим:", "ПРИМЕНЕНИЕ" if a.apply else "СУХОЙ")
ALL = json.load(open(CARDS, encoding='utf-8'))
res = []; failed = []
def do(g):
    global res
    m = groups[g]; movers = [int(r["nmID"]) for r in m if r["действие"] == "перенести"]
    if g.startswith("новая"):
        seed = movers[0]; rest = movers[1:]
        L(g, "новая: разъединить", seed, "затем присоединить", len(rest))
        if not a.apply: return
        old = int(next(r["сейчас_imtID"] for r in m if int(r["nmID"]) == seed))
        alone = sum(1 for c in ALL if c["imtID"] == old) == 1
        if alone:
            tgt = old                                    # карточка уже одна в своей склейке: разъединять не нужно
        else:
            call("/content/v2/cards/moveNm", {"nmIDs": [seed]}); time.sleep(7)
            tgt = old
            for _ in range(24):
                time.sleep(15); tgt = imt_of([seed]).get(seed, old)
                if tgt != old: break
            if tgt == old: raise RuntimeError(f"{g}: разъединение не подтвердилось")
        L("  новый imtID", tgt)
        for i in range(0, len(rest), 30):
            call("/content/v2/cards/moveNm", {"targetIMT": tgt, "nmIDs": rest[i:i+30]}); time.sleep(7)
        want = {nm: tgt for nm in movers}
    else:
        tgt = int(g); L(g, "перенести", len(movers), "в imtID", tgt)
        if not a.apply: return
        for i in range(0, len(movers), 30):
            call("/content/v2/cards/moveNm", {"targetIMT": tgt, "nmIDs": movers[i:i+30]}); time.sleep(7)
        want = {nm: tgt for nm in movers}
    for nm in want: res.append(dict(nmID=nm, цель=g, imtID_цель=want[nm], отправлено="да"))

for g in todo:
    try: do(g)
    except Exception as e: L("  ОШИБКА", g, str(e)[:200]); failed.append(g)
if failed:
    L("второй проход для", len(failed), "групп"); time.sleep(30); f2 = failed; failed = []
    for g in f2:
        try: do(g)
        except Exception as e: L("  ОШИБКА повтор", g, str(e)[:200]); failed.append(g)
if res:
    w = csv.DictWriter(open(D / "results.csv", "w", encoding="utf-8-sig", newline=""), fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
    L("ИТОГ: отправлено карточек", len(res))

L("не применено групп:", len(failed), failed[:10])
