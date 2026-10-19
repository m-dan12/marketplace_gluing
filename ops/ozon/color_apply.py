import csv, collections, json, re, sys, time
from ops.ozon import live_apply as A
exec(open("prev3.py",encoding="utf-8").read().split("rows=[];unres=[]")[0].split("cl={k:v")[0].split("D=json")[0])  # imports only
D=json.load(open("out/twins_full.json",encoding="utf-8"))
P={a:{r["offer_id"]:r for r in csv.DictReader(open(f"logs/20261012-134821-{a}/plan.csv",encoding="utf-8-sig"))} for a in ("proftex","milky","timeless")}
cl=collections.defaultdict(list)
for r in D: cl[(r["acct"],r["grp"],json.dumps(r["sig"],ensure_ascii=False))].append(r)
src=open("prev3.py",encoding="utf-8").read()
ns={}
exec("import re,collections\n"+src[src.index("def g(r,k)"):src.index("rows=[];unres=[]")],ns)
feats,g=ns["feats"],ns["g"]
ops=[];prev=[]
for k,v in cl.items():
    if len(v)<2: continue
    p=P[k[0]]
    if not all(r["offer"] in p for r in v): continue
    if len({p[r["offer"]]["to"] for r in v})==len(v): continue
    # близнецы, которые остались в одной группе: группируем по новой группе
    byto=collections.defaultdict(list)
    for r in v: byto[p[r["offer"]]["to"]].append(r)
    for to,mem in byto.items():
        if len(mem)<2: continue
        F=[feats(r) for r in mem]
        keys=[c for c in dict.fromkeys(x for f in F for x in f) if any(f.get(c) for f in F)]
        chosen=[]
        for c in keys:
            if len({tuple(f.get(x,"") for x in chosen) for f in F})==len(mem): break
            if len({f.get(c,"") for f in F})>1: chosen.append(c)
        tags=[", ".join(x for x in (f.get(c,"") for c in chosen) if x) for f in F]
        if len(set(tags))<len(mem):
            tags=[(t+", " if t else "")+f"вариант {i+1}" for i,t in enumerate(tags)] if len(set(tags))==1 else [f"{t}, вариант {i+1}" if t in tags[:i]+tags[i+1:] else t for i,t in enumerate(tags)]
        for r,t in zip(mem,tags):
            cur=g(r,"Название цвета")
            ops.append(dict(acct=k[0],offer=r["offer"],old=cur,new=(cur+", "+t) if t else cur,cid=r["color_id"][0]))
ops=[o for o in ops if o["new"]!=o["old"]]
# уникальность
chk=collections.defaultdict(set)
json.dump(ops,open("out/color_ops.json","w",encoding="utf-8"),ensure_ascii=False,indent=0)
print(len(ops),collections.Counter(o["acct"] for o in ops)); 
for o in ops[:8]+ops[-4:]: print(o["offer"],"|",o["old"],"->",o["new"])
