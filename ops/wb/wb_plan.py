"""План склеек WB (пример: Профтекс, предмет «Постельное белье», бренд «Сказка»). Без записи на WB.

Логика:
  1. Активные карточки: остаток WB + FBS/DBS > 0.
  2. Ветка по правилам проекта (порядок профиля): KANT → эко-рюши → эко → рюши → детские → обычные.
  3. Внутри ветки группируем по РАЗМЕРУ (ключ артикула): в склейке WB один размер у разных дизайнов.
  4. Размер режем на части не больше 30 карточек, поровну (51 -> 26+25).
  5. Липкость: часть наследует imtID, где уже лежит больше всего её карточек; на месте остаются те, кто влезает.
"""
import csv, json, math, collections, tomllib, sys
from pathlib import Path
from gluing.parsing import parse_article
from gluing.brands import brand_for
from gluing.rules import Context, assign
from gluing.lists import eco_designs, child_designs
from gluing.model import Card

SUBJECT, BRAND, MAXG = "Постельное белье", "Сказка", 30
prof = tomllib.load(open("profiles/wb_proftex.toml", "rb"))
order = [o for o in prof["rules"]["order"] if o not in ("carnival", "decor", "whole_category", "button", "rhomb")]
ctx = Context(prof, eco_designs(), child_designs())
cards_raw = [c for c in json.load(open("out/wb_cards.json", encoding="utf-8")) if c["subjectName"] == SUBJECT and c["brand"] == BRAND]
st = json.load(open("out/wb_stocks.json"))
wb, fbs = ({int(k): v for k, v in st[x].items()} for x in ("wb", "fbs"))

cards, skipped = [], collections.Counter()
for c in cards_raw:
    p = parse_article(c["vendorCode"]); br = brand_for(p["prefix"])
    stock = wb.get(c["nmID"], 0) + fbs.get(c["nmID"], 0)
    if br is None: skipped["неизвестный префикс"] += 1; continue
    if stock <= 0: skipped["нет остатка"] += 1; continue
    k = Card(article=c["vendorCode"], nm_id=c["nmID"], title=c["title"], category=SUBJECT, brand=BRAND, prefix=p["prefix"],
             design=p["design"], key=p["key"], key_nums=p["key_nums"], stock_wb=wb.get(c["nmID"], 0), stock_fbs=fbs.get(c["nmID"], 0))
    k.flags = {c["imtID"]}
    cards.append(k)
print("карточек предмета+бренда:", len(cards_raw), "| в план:", len(cards), "| пропущено:", dict(skipped))

# 2-3: ветка, затем размер
sets = collections.defaultdict(list)
for k in cards:
    b = assign(k, ctx, order)
    sets[(b.bid, b.rule, k.key)].append(k)

# запасная ветка: если в ветке для этого размера меньше min_group карточек, они уходят дальше по цепочке
FALL = {"eco_ruffle": "ruffle", "ruffle": None, "eco": None, "child": None, "kant": None}
MINg = prof["limits"]["min_group"]
by_bid = {(bid, key): rule for (bid, rule, key) in sets}
moved = collections.Counter()
changed = True
while changed:
    changed = False
    for (bid, rule, key), mem in list(sets.items()):
        if bid in FALL and 0 < len(mem) < MINg:
            nb = FALL[bid]
            if nb:
                tgt = next((kk for kk in sets if kk[0] == nb and kk[2] == key), (nb, "Рюши — комплекты", key))
            else:
                tgt = next((kk for kk in sets if kk[0] == f"key:{key}"), (f"key:{key}", f"Ключ {key}", key))
            sets[tgt] = sets.get(tgt, []) + mem; del sets[(bid, rule, key)]; moved[f"{rule} -> {tgt[1]}"] += len(mem); changed = True; break
print("запасная ветка (мало карточек этого размера):", dict(moved))

# 4-5: части и липкость
plan, groups = [], []
used = set()   # один imtID достаётся только одной части
for (bid, rule, key), mem in sorted(sets.items()):
    n = len(mem); parts = math.ceil(n / MAXG); cap = math.ceil(n / parts)
    cur = collections.Counter(next(iter(k.flags)) for k in mem)
    slots = [imt for imt, _ in cur.most_common() if imt not in used][:parts]
    used.update(slots)
    slots += [None] * (parts - len(slots))               # новых частей без imtID
    load = [0] * parts; target = {}
    for k in sorted(mem, key=lambda k: (-(k.stock_wb + k.stock_fbs), k.article)):
        imt = next(iter(k.flags))
        i = slots.index(imt) if imt in slots else None
        if i is None or load[i] >= cap: i = min(range(parts), key=lambda j: load[j])
        target[k.article] = i; load[i] += 1
    for i in range(parts):
        gid = slots[i] or f"новая_{bid}_{key}_{i+1}"
        members = [k for k in mem if target[k.article] == i]
        kind = "склейка" if len(members) >= prof["limits"]["min_group"] else "малая"
        groups.append(dict(группа=gid, ветка=rule, размер_ключ=key, карточек=len(members), вид=kind))
        for k in members:
            cur_imt = next(iter(k.flags))
            plan.append(dict(nmID=k.nm_id, артикул=k.article, название=k.title[:80], ветка=rule, ключ=key, дизайн=k.prefix + k.design,
                             остаток=k.stock_wb + k.stock_fbs, сейчас_imtID=cur_imt, цель=gid, действие="остаётся" if str(cur_imt) == str(gid) else "перенести"))
for name, rows in (("wb_plan_postelnoe.csv", plan), ("wb_groups_postelnoe.csv", groups)):
    w = csv.DictWriter(open(name, "w", encoding="utf-8-sig", newline=""), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
sz = [g["карточек"] for g in groups]
print("групп:", len(groups), "| размеры групп мин/макс:", min(sz), max(sz), "| малых (<6):", sum(g["вид"] == "малая" for g in groups))
print("ветки:", dict(collections.Counter(p["ветка"] for p in plan)))
print("остаются на месте:", sum(p["действие"] == "остаётся" for p in plan), "из", len(plan), "| переносим:", sum(p["действие"] == "перенести" for p in plan))
cur_groups = {p["сейчас_imtID"] for p in plan}
mixed = sum(1 for imt in cur_groups if len({p["ключ"] for p in plan if p["сейчас_imtID"] == imt}) > 1)
print("сейчас групп:", len(cur_groups), "| из них со смешением размеров:", mixed)
