from ops import ENV_PATH
import json, time, csv, datetime as dt
from ops.ozon import live_apply as A
env = A.read_env(ENV_PATH)
ops=json.load(open("out/color_ops.json",encoding="utf-8"))
for o in ops: o["new"]=o["new"].replace("на пуговицах/без молнии","без молнии")
stamp=dt.datetime.now().strftime("%Y%m%d-%H%M%S"); res=[]
for acct,sfx in (("proftex",""),("milky","_MILKY"),("timeless","_TIMELESS")):
    oz=A.Ozon(env["OZON_CLIENT_ID"+sfx],env["OZON_API_KEY"+sfx])
    mine=[o for o in ops if o["acct"]==acct]
    def put(lst):
        items=[{"offer_id":o["offer"],"attributes":[{"id":10097,"complex_id":0,"values":[{"value":o["new"]}]}]} for o in lst]
        t=oz.post("/v1/product/attributes/update",{"items":items})["task_id"]
        for _ in range(40):
            time.sleep(8)
            r=oz.post("/v1/product/import/info",{"task_id":t})["result"]["items"]
            if len(r)>=len(items) and all(x["status"] in("imported","failed","skipped") for x in r): break
        return {x["offer_id"]:x for x in r}
    probe=put(mine[:1]); print(acct,"проба",[(k,v["status"],v["errors"]) for k,v in probe.items()])
    if any(v["status"]!="imported" for v in probe.values()): break
    by=dict(probe)
    for i in range(1,len(mine),100): by.update(put(mine[i:i+100]))
    for o in mine: res.append({**o,"status":by.get(o["offer"],{}).get("status","no_status"),"error":str(by.get(o["offer"],{}).get("errors",""))})
    print(acct,{s:sum(r["status"]==s for r in res if r["acct"]==acct) for s in {r["status"] for r in res}})
w=csv.DictWriter(open(f"logs/color_{stamp}.csv","w",encoding="utf-8-sig",newline=""),fieldnames=list(res[0]));w.writeheader();w.writerows(res)
