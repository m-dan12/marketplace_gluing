"""Освобождает место в целевых группах: карточки, уходящие по плану в другую группу, временно отделяются (moveNm без targetIMT)."""
from ops import ENV_PATH, MERGE_ENV_PATH
from ops.common import read_env
import csv, json, time, os, collections, urllib.request
ACCT = os.environ.get("WB_ACCT", "proftex"); SUF = "" if ACCT == "proftex" else "_" + ACCT
MT = {"proftex": "SK", "milky": "MG", "timeless": "TL"}[ACCT]
env = read_env(MERGE_ENV_PATH); tok = env["WB_MERGE_TOKEN_" + MT]
cards = json.load(open(f"out/wb_cards{SUF}.json", encoding="utf-8"))
plan = {int(r["nmID"]): r for r in csv.DictReader(open(f"wb_plan_all{SUF}.csv", encoding="utf-8-sig"))}
size = collections.Counter(c["imtID"] for c in cards)
incoming = collections.Counter(r["цель"] for r in plan.values() if r["действие"] == "перенести")
tight = {g for g, n in incoming.items() if not g.startswith("новая") and size[int(g)] + n > 30}
leavers = collections.defaultdict(list)
for c in cards:
    r = plan.get(c["nmID"])
    if r and str(c["imtID"]) in tight and r["цель"] != str(c["imtID"]): leavers[c["imtID"]].append(c["nmID"])
print("тесных групп:", len(tight), "| отделить карточек:", sum(len(v) for v in leavers.values()))
for g, nms in leavers.items():
    for i in range(0, len(nms), 30):
        req = urllib.request.Request("https://content-api.wildberries.ru/content/v2/cards/moveNm", data=json.dumps({"nmIDs": nms[i:i+30]}).encode(), headers={"Authorization": tok, "Content-Type": "application/json"})
        for t in range(8):
            try: json.load(urllib.request.urlopen(req, timeout=120)); break
            except urllib.error.HTTPError as e:
                if e.code == 429: time.sleep(20 * (t + 1)); continue
                print(g, "ошибка", e.read().decode()[:120]); break
        time.sleep(7)
