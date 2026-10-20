"""Выгрузка WB Профтекс: карточки (content-api), остатки WB (seller-analytics) и FBS/DBS (marketplace-api), рулоны Селсапа."""
from ops import ENV_PATH
from ops.common import read_env, selsup_rolls
import json, time, urllib.request, urllib.error, collections
env = read_env(ENV_PATH); import os
ACCT = os.environ.get("WB_ACCT", "proftex"); SFX = {"proftex": "", "milky": "_MILKY", "timeless": "_TIMELESS"}[ACCT]
key = env["WB_API_KEY" + SFX]
SUF = "" if ACCT == "proftex" else "_" + ACCT
def call(url, body=None):
    for t in range(10):
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers={"Authorization": key, "Content-Type": "application/json"})
        try: return json.load(urllib.request.urlopen(req, timeout=180))
        except urllib.error.HTTPError as e:
            if e.code == 429: time.sleep(25 * (t + 1)); continue
            raise
    raise RuntimeError("429 " + url)
cards, cur = [], {"limit": 100}
while True:
    r = call("https://content-api.wildberries.ru/content/v2/get/cards/list", {"settings": {"cursor": cur, "filter": {"withPhoto": -1}}})
    cards += r["cards"]
    if len(r["cards"]) < 100: break
    cur = {"limit": 100, "updatedAt": r["cursor"]["updatedAt"], "nmID": r["cursor"]["nmID"]}
json.dump(cards, open(f"out/wb_cards{SUF}.json", "w", encoding="utf-8"), ensure_ascii=False)
items, off = [], 0
while True:
    r = call("https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses", {"limit": 250000, "offset": off})["data"]["items"]
    items += r
    if len(r) < 250000: break
    off += 250000
wb = collections.Counter()
for i in items: wb[i["nmId"]] += i["quantity"]
sk = [(c["nmID"], s["skus"][0]) for c in cards for s in c["sizes"] if s["skus"]]
fbs = collections.Counter()
for w in [w["id"] for w in call("https://marketplace-api.wildberries.ru/api/v3/warehouses") if not w.get("isDeleting")]:
    for i in range(0, len(sk), 1000):
        ch = sk[i:i + 1000]; m = {b: n for n, b in ch}
        for x in call(f"https://marketplace-api.wildberries.ru/api/v3/stocks/{w}", {"skus": [b for _, b in ch]})["stocks"]: fbs[m[x["sku"]]] += x["amount"]
rolls = selsup_rolls(env["SELSUP_API_TOKEN"]) if ACCT == "proftex" else {}
json.dump({"wb": dict(wb), "fbs": dict(fbs), "rolls": rolls}, open(f"out/wb_stocks{SUF}.json", "w"))
print("карточек", len(cards), "| WB-остаток у", sum(v > 0 for v in wb.values()), "| FBS у", sum(v > 0 for v in fbs.values()), "| рулонов", len(rolls))
