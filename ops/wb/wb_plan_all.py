"""План склеек WB, весь кабинет Профтекс. Без записи. Логика в docs/wb_logic.md."""

import csv, json, math, collections, tomllib, re, os
ACCT = os.environ.get("WB_ACCT", "proftex"); SUF = "" if ACCT == "proftex" else "_" + ACCT
from gluing.parsing import parse_article
from gluing.brands import brand_for
from gluing.rules import Context, assign
from gluing.lists import eco_designs, child_designs
from gluing.model import Card
MAXG = 26   # рабочий лимит; запас до жёсткого лимита WB (30) под новые товары
prof = tomllib.load(open("profiles/wb_proftex.toml", "rb")); MINg = prof["limits"]["min_group"]
order = prof["rules"]["order"]
if ACCT != "proftex":                                  # списки эко/детских и рюши-бренды ведутся только для Профтекса
    prof["rules"]["ruffle_brands"] = ["Milky Garden", "Timeless", "TimeLess", "MILKY GARDEN"]
    prof["rules"]["whole_category_brands"] = {}; prof["rules"]["no_eco_brands"] = []
    ctx = Context(prof, frozenset(), frozenset())
else:
    ctx = Context(prof, eco_designs(), child_designs())
raw = json.load(open(f"out/wb_cards{SUF}.json", encoding="utf-8"))
st = json.load(open(f"out/wb_stocks{SUF}.json")); wb = {int(k): v for k, v in st["wb"].items()}; fbs = {int(k): v for k, v in st["fbs"].items()}; rolls = st["rolls"]
CUT = re.compile(r"^1-(\d+)00220$")
imt_members = collections.defaultdict(set)         # все карточки imtID, в т.ч. неактивные
for c in raw: imt_members[c["imtID"]].add(c["nmID"])
cards, skipped = [], collections.Counter()
for c in raw:
    p = parse_article(c["vendorCode"])
    if brand_for(p["prefix"]) is None: skipped["неизвестный префикс"] += 1; continue
    if not p["key"]: skipped["нет ключа в артикуле"] += 1; continue
    k = Card(article=c["vendorCode"], nm_id=c["nmID"], title=c["title"], category=c["subjectName"], brand=c["brand"], prefix=p["prefix"],
             design=p["design"], key=p["key"], key_nums=p["key_nums"], stock_wb=wb.get(c["nmID"], 0), stock_fbs=fbs.get(c["nmID"], 0))
    if c["subjectName"] == "Ткани для рукоделия":
        m = CUT.match(p["key"]); code = p["prefix"] + p["design"]
        if m and code in rolls: k.stock_roll = rolls[code] // int(m.group(1))
    if k.stock_total <= 0: skipped["нет остатка"] += 1; continue
    k.flags = {c["imtID"]}
    cards.append(k)
print("карточек всего", len(raw), "| в план", len(cards), "| пропущено", dict(skipped))

sets = collections.defaultdict(list)
for k in cards:
    b = assign(k, ctx, order)
    sets[(b.bid, b.rule, k.category, k.brand, k.key)].append(k)
FALL = {"eco_ruffle": "ruffle", "ruffle": None, "eco": None, "child": None, "kant": None}
moved = collections.Counter(); changed = True
while changed:
    changed = False
    for key_, mem in list(sets.items()):
        bid, rule, cat, br, key = key_
        if bid in FALL and 0 < len(mem) < MINg:
            nb = FALL[bid]
            tgt = next((kk for kk in sets if kk[0] == (nb or f"key:{key}") and kk[2:] == (cat, br, key)), None) or \
                  ((nb, "Рюши — комплекты", cat, br, key) if nb else (f"key:{key}", f"Ключ {key}", cat, br, key))
            sets[tgt] = sets.get(tgt, []) + mem; del sets[key_]; moved[f"{rule}->{tgt[1]}"] += len(mem); changed = True; break
planned = {k.nm_id for k in cards}
used = set(); plan, groups = [], []
for (bid, rule, cat, br, key), mem in sorted(sets.items(), key=lambda x: (-len(x[1]), x[0])):
    n = len(mem)
    cnt = collections.Counter(next(iter(k.flags)) for k in mem)
    cand = [i for i, _ in cnt.most_common() if i not in used]
    cap_of = lambda imt: MAXG - len(imt_members[imt] - planned)      # место с учётом неактивных карточек, остающихся в imt
    slots, tot = [], 0
    for imt in cand:
        if tot >= n: break
        if cap_of(imt) > 0: slots.append(imt); tot += cap_of(imt)
    while tot < n: slots.append(None); tot += MAXG
    parts = len(slots); parts_min = max(math.ceil(n / MAXG), 1)
    cap = [min(cap_of(i) if i else MAXG, math.ceil(n / max(parts, parts_min))) for i in slots]
    while sum(cap) < n:
        j = max(range(parts), key=lambda j: (cap_of(slots[j]) if slots[j] else MAXG) - cap[j]); cap[j] += 1
    used.update(i for i in slots if i)
    load = [0] * parts; tgt = {}
    for k in sorted(mem, key=lambda k: (-k.stock_total, k.article)):
        imt = next(iter(k.flags)); i = slots.index(imt) if imt in slots else None
        if i is None or load[i] >= cap[i]:
            i = max((j for j in range(parts) if load[j] < cap[j]), key=lambda j: cap[j] - load[j])
        tgt[k.nm_id] = i; load[i] += 1
    for i in range(parts):
        members = [k for k in mem if tgt[k.nm_id] == i]
        if not members: continue
        gid = slots[i] or f"новая|{cat}|{br}|{key}|{rule}|{i+1}"
        groups.append(dict(группа=gid, предмет=cat, бренд=br, ветка=rule, размер_ключ=key, карточек=len(members), вид="склейка" if len(members) >= MINg else "малая"))
        for k in members:
            cur = next(iter(k.flags))
            plan.append(dict(nmID=k.nm_id, артикул=k.article, предмет=cat, бренд=br, ветка=rule, ключ=key, остаток=k.stock_total, сейчас_imtID=cur, цель=gid,
                             действие="остаётся" if str(cur) == str(gid) else "перенести"))
for name, rows in ((f"wb_plan_all{SUF}.csv", plan), (f"wb_groups_all{SUF}.csv", groups)):
    w = csv.DictWriter(open(name, "w", encoding="utf-8-sig", newline=""), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
sz = [g["карточек"] for g in groups]
print("групп", len(groups), "| размер мин/макс", min(sz), max(sz), "| малых", sum(g["вид"] == "малая" for g in groups), "| одиночек", sum(s == 1 for s in sz))
print("остаётся", sum(p["действие"] == "остаётся" for p in plan), "| перенести", sum(p["действие"] == "перенести" for p in plan))
byg = collections.defaultdict(set)
for p in plan: byg[p["цель"]].add(p["ключ"])
print("групп со смешением размеров", sum(len(v) > 1 for v in byg.values()), "| предметы:", dict(collections.Counter(g["предмет"] for g in groups)))
print("групп > 30 с учётом неактивных:", sum(1 for g in groups if str(g["группа"]).isdigit() and g["карточек"] + len(imt_members[int(g["группа"])] - planned) > MAXG))
