from ops import ENV_PATH
import csv, json, time, urllib.request, collections, datetime as dt, sys
env = {}
for l in open(ENV_PATH, encoding="utf-8-sig"):
    if "=" in l and not l.startswith("#"):
        k, v = l.rstrip("\r\n").split("=", 1); env[k.strip()] = v.strip().strip('"')
H = {"Client-Id": env["OZON_CLIENT_ID"], "Api-Key": env["OZON_API_KEY"], "Content-Type": "application/json"}
def post(path, body):
    for a in range(4):
        try:
            r = urllib.request.Request("https://api-seller.ozon.ru" + path, json.dumps(body).encode(), H)
            return json.load(urllib.request.urlopen(r, timeout=90))
        except Exception as e:
            err = e; time.sleep(2 + a * 2)
    raise SystemExit(f"{path}: {type(err).__name__} {getattr(err,'code','')}")
to = dt.datetime.utcnow(); since = to - dt.timedelta(days=28)
f = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
cnt = collections.Counter()
for path, key in (("/v3/posting/fbs/list", "postings"), ("/v2/posting/fbo/list", None)):
    off = 0
    while True:
        body = {"dir": "ASC", "filter": {"since": f(since), "to": f(to)}, "limit": 1000, "offset": off}
        r = post(path, body)["result"]
        ps = r[key] if key else r
        for p in ps:
            if p.get("status") == "cancelled": continue
            for pr in p.get("products", []): cnt[pr["offer_id"]] += pr.get("quantity", 1)
        print(path, off, len(ps), file=sys.stderr)
        if len(ps) < 1000: break
        off += 1000
rows = list(csv.DictReader(open("out/ozon_proftex.csv", encoding="utf-8")))
for r in rows: r["orders28"] = cnt.get(r["offer_id"], 0)
w = csv.DictWriter(open("out/ozon_proftex.csv", "w", encoding="utf-8", newline=""), fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
print("артикулов с заказами:", len(cnt), "штук:", sum(cnt.values()), file=sys.stderr)
