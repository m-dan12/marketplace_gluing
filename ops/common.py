"""Общие помощники рабочих скриптов."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    env = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def selsup_rolls(token: str) -> dict[str, int]:
    """Рулоны PTxxxx на складе FBS Селсапа (id 10001), метры."""
    def get(path, params):
        q = urllib.parse.urlencode(params, doseq=True)
        for a in range(4):
            try:
                req = urllib.request.Request("https://api.selsup.ru" + path + "?" + q, headers={"Authorization": token})
                return json.load(urllib.request.urlopen(req, timeout=90))
            except Exception:
                time.sleep(2 + a * 3)
        raise RuntimeError("selsup: " + path)
    rows = [r for r in get("/api/wms/stock/all", {"warehouseId": 10001}) if r.get("quantity")]
    pid = lambda r: ((r.get("sku") or {}).get("product") or {}).get("id") or r.get("skuId")
    ids = list({pid(r) for r in rows if pid(r)})
    info = {}
    for i in range(0, len(ids), 200):
        for x in get("/api/product/find", {"ids": ids[i:i + 200], "limit": 200}).get("rows", []):
            info[x["id"]] = x
    m: collections.Counter = collections.Counter()
    for r in rows:
        art = ((r.get("sku") or {}).get("product") or {}).get("anyArticle") or \
              (((info.get(pid(r)) or {}).get("view") or {}).get("model") or {}).get("article")
        if art and re.fullmatch(r"PT\d+", art):
            m[art] += float(r["quantity"])
    return {a: int(q) for a, q in m.items()}


# ---------- выгрузка кабинета ----------
