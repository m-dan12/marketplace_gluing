import csv, json, sys, time
from ops.ozon.ozapi import post
ops = [o for o in csv.DictReader(open("out/dryrun_ops.csv", encoding="utf-8")) if "|Ткань|" in o["group_key"]]
offers = [o["offer_id"] for o in ops]
def read(offs):
    out = {}
    for i in range(0, len(offs), 100):
        r = post("/v4/product/info/attributes", {"filter": {"offer_id": offs[i:i+100], "visibility": "ALL"}, "limit": 100})
        for p in r["result"]: out[p["offer_id"]] = {a["id"]: a["values"] for a in p["attributes"]}
    return out
def model(a): v = a.get(9048); return v[0]["value"] if v else None
def upd(batch):
    return post("/v1/product/attributes/update", {"items": [{"offer_id": o["offer_id"], "attributes": [{"complex_id": 0, "id": 9048, "values": [{"value": o["to"]}]}]} for o in batch]})
def wait(tid):
    for _ in range(30):
        items = post("/v1/product/import/info", {"task_id": tid}).get("result", {}).get("items", [])
        if items and all(x["status"] in ("imported", "failed", "skipped") for x in items): return items
        time.sleep(10)
    return items
mode = sys.argv[1]
if mode == "snap":
    before = read(offers)
    json.dump(before, open("out/fabric_before.json", "w", encoding="utf-8"), ensure_ascii=False)
    bad = [o["offer_id"] for o in ops if model(before.get(o["offer_id"], {})) != o["from"]]
    print("карточек считано:", len(before), "из", len(offers), "| расхождений с выгрузкой:", len(bad))
elif mode == "one":
    o = ops[0]; items = wait(upd([o])["task_id"]); print(items)
    time.sleep(20); a = read([o["offer_id"]])[o["offer_id"]]; b = json.load(open("out/fabric_before.json", encoding="utf-8"))[o["offer_id"]]
    print("модель была:", o["from"], "стала:", model(a), "ожидалось:", o["to"])
    print("другие атрибуты изменились:", [i for i in b if str(i) != "9048" and b[i] != a.get(int(i), a.get(i))])
if mode == "all":
    rest = ops[1:]; fails = []
    for i in range(0, len(rest), 100):
        b = rest[i:i+100]; r = upd(b)
        if "task_id" not in r: print("ОШИБКА, остановка:", r); break
        items = wait(r["task_id"]); f = [x for x in items if x["status"] != "imported"]
        fails += f; print(f"пакет {i//100+1}: {len(items)} шт, ошибок {len(f)}", flush=True)
        if f: print(f[:3]); break
    print("всего ошибок:", len(fails))
if mode == "verify":
    time.sleep(30); now = read(offers); want = {o["offer_id"]: o["to"] for o in ops}
    wrong = [x for x in want if model(now.get(x, {})) != want[x]]
    print("в нужной модели:", len(want) - len(wrong), "из", len(want), "| не совпало:", wrong[:5])
    import collections; print(collections.Counter(want.values()))
    b = json.load(open("out/fabric_before.json", encoding="utf-8"))
    ch = [x for x in b if any(b[x][i] != now[x].get(int(i), now[x].get(i)) for i in b[x] if str(i) != "9048")]
    print("карточек с изменением других атрибутов:", len(ch))
