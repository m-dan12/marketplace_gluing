from ops import ENV_PATH
import json, time, urllib.request
env = {}
for l in open(ENV_PATH, encoding="utf-8-sig"):
    if "=" in l and not l.startswith("#"):
        k, v = l.rstrip("\r\n").split("=", 1); env[k.strip()] = v.strip().strip('"')
import os
SFX = os.environ.get("OZ_SFX", "")
H = {"Client-Id": env["OZON_CLIENT_ID" + SFX], "Api-Key": env["OZON_API_KEY" + SFX], "Content-Type": "application/json"}
def post(path, body):
    r = urllib.request.Request("https://api-seller.ozon.ru" + path, json.dumps(body).encode(), H)
    try: return json.load(urllib.request.urlopen(r, timeout=90))
    except urllib.error.HTTPError as e: return {"_http": e.code, "_body": e.read().decode()[:500]}
