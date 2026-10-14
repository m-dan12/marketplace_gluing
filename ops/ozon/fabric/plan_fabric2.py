import csv, json, tomllib, collections
exec(open("plan_full.py", encoding="utf-8").read().split("for whole in (False, True):")[0])   # build_all, live, rows, prof
designs, unk = build_all(True); designs = [d for d in designs if d.typ == "Ткань"]
applied = {}                                                    # дизайн -> ключ группы, уже применённый
for o in csv.DictReader(open("out/dryrun_ops.csv", encoding="utf-8")):
    if "|Ткань|" in o["group_key"]:
        s = [x for x in rows[o["offer_id"]]["offer_id"].split("/")][0]
        applied[s] = o["group_key"]
name = {"proftex|Сказка|Ткань|—#1": "g_2c528770", "proftex|Сказка|Ткань|—#2": "g_5c3c00e0", "proftex|Сказка|Ткань|—#3": "g_b86dc585", "proftex|Сказка|Ткань|—#4": "g_a20c9eae"}
assign = {d.uid: applied[d.design] for d in designs if d.design in applied}
print("дизайнов ткани:", len(designs), "уже применено:", len(assign), "новых:", len(designs) - len(assign))
groups, log, st = build(designs, prof, {"decisions": {}, "assign": assign})
print([(g.key.split("|")[-1], len(g.designs), sum(len(d.skus) for d in g.designs)) for g in groups])
ops = []
for g in groups:
    to = name.get(g.key)
    for d in g.designs:
        for s in d.skus: ops.append({"offer_id": s.offer_id, "product_id": rows[s.offer_id]["product_id"], "group_key": g.key, "to": to, "design": d.design})
print("групп без имени:", [g.key for g in groups if g.key not in name])
moved = [d for g in groups for d in g.designs if assign.get(d.uid) not in (None, g.key)]
print("применённых дизайнов, сменивших группу:", len(moved))
json.dump(ops, open("out/fabric2_ops.json", "w", encoding="utf-8"), ensure_ascii=False)
