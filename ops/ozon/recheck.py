from ops import ENV_PATH
import csv, glob, os, collections, json
from ops.ozon import live_apply as A
env = A.read_env(ENV_PATH)
tot = {}
for acct, sfx in A.ACCOUNTS.items():
    d = sorted(glob.glob(f"logs/20261009-190720-{acct}"))
    if not d: continue
    res = list(csv.DictReader(open(d[0] + "/results.csv", encoding="utf-8-sig")))
    bad = [r for r in res if r["verified"] != "ok"]
    oz = A.Ozon(env["OZON_CLIENT_ID" + sfx], env["OZON_API_KEY" + sfx])
    now = A.read_models(oz, [r["offer_id"] for r in bad])
    still = [r for r in bad if A.model_of(now.get(r["offer_id"], {})) != r["to"]]
    print(acct, "всего", len(res), "| было не совпало", len(bad), "| дошло позже", len(bad) - len(still), "| всё ещё не совпало", len(still))
    print("  причины:", collections.Counter((r["error"][:60] or r["status"]) for r in still).most_common(5))
    with open(d[0] + "/still_bad.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(res[0].keys())); w.writeheader(); w.writerows(still)
