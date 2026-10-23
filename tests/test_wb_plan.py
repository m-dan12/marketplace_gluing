from gluing.wb.loader import context_for, load_cards
from gluing.wb.planner import build_wb
from gluing.wb.profile import load_profile
from planner.store import MemoryStore
from planner.wb_plan import assemble_plan, build_wb_groups
from applier.wb_ops import dry_run_wb

ROLLS = {"PT110": 20}


def row(art, nm, stock=5, imt=None, brand="Сказка", cat="Наволочки", title=""):
    return {"offer_id": art, "nm_id": nm, "title": title, "category": cat, "wb_brand": brand, "imt_id": imt,
            "stock_wb": stock, "stock_fbs": 0, "orders30": 1}


def make(rows, name="wb_proftex"):
    prof = load_profile(name)
    ld = load_cards(rows, ROLLS, prof)
    members = {}
    for r in rows:
        if r["imt_id"] is not None:
            members.setdefault(r["imt_id"], set()).add(r["nm_id"])
    res = build_wb(ld.cards, ld.live, members, prof, context_for(prof))
    return prof, ld, res


def test_loader_brand_by_prefix_and_checks_against_wb_brand():
    rows = [row("PA6273/0-0-22/1", 1, brand="Сказка"), row("PA5929/0-0-26/1", 2, brand="Нет бренда"), row("PT1/0-0-26/1", 3, brand="Мечта!"),
            row("ZZ9/0-0-26/1", 4), row("PT2/0-0-26/1", 5, stock=0), row("PT3/нет", 6), row("ZZ8/0-0-26/1", 7, stock=0)]
    ld = load_cards(rows, ROLLS, load_profile("wb_proftex"))
    assert {c.nm_id: c.brand for c in ld.cards} == {1: "Анна Мария", 2: "Анна Мария", 3: "Сказка"}
    assert [m[0] for m in ld.brand_mismatch] == ["PA6273/0-0-22/1", "PA5929/0-0-26/1", "PT1/0-0-26/1"]
    assert ld.skipped == {"неизвестный префикс": 1, "нет остатка": 2, "нет ключа в артикуле": 1}   # неактивные неизвестные не считаются


def test_fabric_cut_from_roll():
    rows = [row("PT110/1-500220", 1, stock=0, cat="Ткани для рукоделия")]
    ld = load_cards(rows, ROLLS, load_profile("wb_proftex"))
    assert ld.cards[0].stock_roll == 4                      # 20 м рулона / отрез 5 м


def test_plan_from_rows_validates_and_roundtrips():
    rows = [row(f"PT{i}/0-0-26/1", 1000 + i, imt=7 if i < 10 else None) for i in range(30)]
    prof, ld, res = make(rows)
    groups = build_wb_groups("skazka", res, ld.live)
    plan = assemble_plan(groups, {"skazka": prof}, {"skazka": ld}, run_id="2026-10-21-wb", engine_version="t",
                         inputs={"as_of": "2026-10-21"}, profile_name="wb_proftex")
    assert plan.run.platform == "wb" and not plan.checks.hard_violations
    store = MemoryStore()
    store.save_draft(plan)
    assert store.load("2026-10-21-wb").groups[0].target["wb"]["imt_id"] in (7, None)
    live = {("skazka", r["nm_id"]): r["imt_id"] or 99 for r in rows}
    dr = dry_run_wb(plan, live)
    assert not dr.blocked


def test_mixed_sizes_in_one_group_is_hard_violation():
    rows = [row(f"PT{i}/0-0-26/1", 1000 + i) for i in range(10)] + [row(f"PT{i}/0-0-20/1", 2000 + i) for i in range(10)]
    prof, ld, res = make(rows)
    groups = build_wb_groups("skazka", res, ld.live)
    # ломаем план вручную: переносим карточку другого размера в чужую группу
    g0, g1 = groups[0], groups[1]
    g0.designs[0].members.append(g1.designs[0].members.pop(0))
    plan = assemble_plan(groups, {"skazka": prof}, {"skazka": ld}, run_id="r", engine_version="t", inputs={}, profile_name="p")
    assert any("несколько размеров" in v for v in plan.checks.hard_violations)


def test_other_cabinet_profile_has_no_lists_and_own_ruffle_brand():
    p = load_profile("wb_milky_garden")
    assert p["cabinet"] == "milky_garden" and p["rules"]["ruffle_brands"] == ["Milky Garden"] and p["rules"]["whole_category_brands"] == {}
    rows = [row(f"MY{i}/0-0-13/1", 1000 + i, brand="Milky Garden") for i in range(8)]
    prof, ld, res = make(rows, "wb_milky_garden")
    assert res.groups[0].bid == "ruffle"                    # у Milky Garden рюши — отдельная ветка


def test_live_rows_deduplicate_overlapping_pages():
    from planner.wb_live import rows_from_cards
    cards = [{"nmID": 1, "imtID": 10, "vendorCode": "A", "characteristics": [{"name": "Цвет", "value": ["красный"]}]},
             {"nmID": 1, "imtID": 11, "vendorCode": "A"}, {"nmID": 2, "imtID": 12, "vendorCode": "B"}, {"nmID": 3}]
    rows = rows_from_cards("skazka", cards)
    assert [(r[1], r[3]) for r in rows] == [(1, 11), (2, 12)]


def test_carnival_without_key_is_glued_by_garment_type():
    rows = [row(f"PT{i}/сарафан", 1000 + i, cat="Карнавальные костюмы") for i in range(7)] +            [row(f"PT{i}/юбка", 2000 + i, cat="Карнавальные костюмы") for i in range(7)]
    prof, ld, res = make(rows)
    assert not ld.no_key and not ld.skipped
    assert sorted(g.bid for g in res.groups) == ["carnival:сарафаны", "carnival:юбки"]
    groups = build_wb_groups("skazka", res, ld.live)
    plan = assemble_plan(groups, {"skazka": prof}, {"skazka": ld}, run_id="r", engine_version="t", inputs={}, profile_name="p")
    assert not plan.checks.hard_violations


def test_loader_grace_period_after_stock_out():
    prof = load_profile("wb_proftex")
    assert prof["stock"]["grace_days"] == 3
    rows = [{**row(f"PT{i}/0-0-26/1", i, stock=0), "out_days": d} for i, d in ((1, 2), (2, 3), (3, 4), (4, None))]
    ld = load_cards(rows, ROLLS, prof)
    assert [c.nm_id for c in ld.cards] == [1, 2]            # обнулились 2 и 3 дня назад: ещё в плане; 4 дня и «не было остатка» — нет
    assert ld.skipped == {"нет остатка": 2}
