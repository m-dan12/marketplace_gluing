from contract import Check, Design, Group, Member, Plan, RunInfo
from applier.ops import BRAND_ATTR, COLOR_NAME_ATTR, MODEL_NAME_ATTR, dry_run

CAT = (1, 2)
ASP = {CAT: {COLOR_NAME_ATTR: "Название цвета", 6771: "Размер простыни"}}


def plan(groups, twins=None):
    return Plan(run=RunInfo(run_id="r", platform="ozon", created_at="2026-10-12T00:00:00Z", engine_version="t",
                            profile_name="ozon", profile_hash="h"),
                groups=groups, checks=Check(twins=twins or []))


def group(gid, offers, designs=1):
    ds = [Design(design=f"PT{i}", members=[Member(offer_id=o) for o in offers[i::designs]]) for i in range(designs)]
    return Group(group_id=gid, key=gid, account="skazka", brand="Сказка", type="Комплект", branch="Обычные", kind="glue",
                 target={"ozon": {"model_name": gid}}, designs=ds)


def live_item(color, model="", cat=CAT, size="160x200"):
    attrs = {str(COLOR_NAME_ATTR): [color], "6771": [size]}
    if model:
        attrs[str(MODEL_NAME_ATTR)] = [model]
    return {"cat": cat, "attrs": attrs, "product_id": 1}


def test_ops_only_for_changes_and_idempotent():
    p = plan([group("g_1", ["a", "b"], 2)])
    live = {("skazka", "a"): live_item("Роза", "g_1"), ("skazka", "b"): live_item("Лилия", "old")}
    r = dry_run(p, live, ASP)
    assert [(o.offer_id, o.from_value, o.to_value) for o in r.ops] == [("b", "old", "g_1")]
    assert r.already_ok == 1 and not r.blocked


def test_group_blocked_when_card_missing_or_mixed_categories():
    p = plan([group("g_1", ["a", "b"], 2), group("g_2", ["c", "d"], 2)])
    live = {("skazka", "a"): live_item("Роза"), ("skazka", "c"): live_item("Роза"),
            ("skazka", "d"): live_item("Лилия", cat=(9, 9))}
    r = dry_run(p, live, ASP)
    assert {b.group_id for b in r.blocked} == {"g_1", "g_2"} and not r.ops


def test_live_twins_block_group_unless_color_fix_planned():
    g = group("g_1", ["a", "b"], 2)
    live = {("skazka", "a"): live_item("Бисер"), ("skazka", "b"): live_item("Бисер")}
    assert dry_run(plan([g]), live, ASP).blocked                     # близнецы, правки нет
    tw = [{"group_id": "g_1", "proposals": [{"design": "PT1", "offers": ["b"], "attr": "Название цвета", "new_value": "Бисер 2"}]}]
    r = dry_run(plan([g], tw), live, ASP)
    assert not r.blocked
    assert {(o.offer_id, o.op_type, o.to_value) for o in r.ops} == {
        ("b", "set_color_name", "Бисер 2"), ("a", "set_model_name", "g_1"), ("b", "set_model_name", "g_1")}


def test_target_name_held_by_unplanned_offers_is_a_warning_only():
    p = plan([group("g_1", ["a"], 1)])
    live = {("skazka", "a"): live_item("Роза"), ("skazka", "z"): live_item("Лилия", "g_1")}   # z не в плане
    r = dry_run(p, live, ASP)
    assert not r.blocked and any("вне плана" in w for w in r.warnings)


def test_target_name_held_by_offers_moving_to_another_group_is_fine():
    p = plan([group("g_1", ["a"], 1), group("g_2", ["z"], 1)])
    live = {("skazka", "a"): live_item("Роза", "g_1"), ("skazka", "z"): live_item("Лилия", "g_1")}   # z уйдёт в g_2
    r = dry_run(p, live, ASP)
    assert not r.blocked and not r.warnings
    assert [(o.offer_id, o.to_value) for o in r.ops] == [("z", "g_2")]


def test_unglue_removes_sold_out_offers_from_plan_models():
    p = plan([group("g_1", ["a"], 1)])
    live = {("skazka", "a"): live_item("Роза", "g_1"),
            ("skazka", "z"): live_item("Лилия", "g_1"),            # вышел из плана, остатка нет → разлепить
            ("skazka", "y"): live_item("Мак", "g_1"),              # вне плана, но остаток есть → не трогаем
            ("skazka", "w"): live_item("Ирис", "w"),               # уже сам по себе
            ("skazka", "v"): live_item("Тюльпан", "other")}        # чужая модель, не из плана
    stock = {("skazka", "z"): 0, ("skazka", "y"): 7, ("skazka", "w"): 0, ("skazka", "v"): 0}
    r = dry_run(p, live, ASP, stock)
    un = [o for o in r.ops if o.op_type == "unglue_model"]
    assert [(o.offer_id, o.from_value, o.to_value) for o in un] == [("z", "g_1", "z")]


def test_no_unglue_without_stock_data():
    p = plan([group("g_1", ["a"], 1)])
    live = {("skazka", "a"): live_item("Роза", "g_1"), ("skazka", "z"): live_item("Лилия", "g_1")}
    assert not [o for o in dry_run(p, live, ASP).ops if o.op_type == "unglue_model"]


def test_select_ops_filters_by_account_type_and_kind():
    import pytest
    from applier.ops import Op, select_ops
    ops = [Op("skazka", "a", "set_model_name", 9048, "", "g1", "g1"), Op("skazka", "b", "set_model_name", 9048, "", "g2", "g2"),
           Op("timeless", "c", "set_color_name", 10097, "x", "y", "g1"), Op("skazka", "z", "unglue_model", 9048, "g1", "z", "")]
    gt = {"g1": "Комплект", "g2": "Скатерть"}
    assert [o.offer_id for o in select_ops(ops, gt)] == ["a", "b", "c", "z"]
    assert [o.offer_id for o in select_ops(ops, gt, only={"unglue_model"})] == ["z"]
    assert [o.offer_id for o in select_ops(ops, gt, accounts={"skazka"}, only={"set_model_name"})] == ["a", "b"]
    assert [o.offer_id for o in select_ops(ops, gt, types={"Скатерть"})] == ["b"]        # снятия по типу не фильтруются
    with pytest.raises(ValueError):
        select_ops(ops, gt, only={"drop_table"})


BM = {"Сказка": "Сказка", "Нет бренда": "Нет бренда"}


def with_brand(item, text, bid):
    item = {**item, "attrs": dict(item["attrs"]), "brand_id": bid}
    if text:
        item["attrs"][str(BRAND_ATTR)] = [text]
    return item


def test_set_brand_majority_id_empty_replaced_foreign_untouched():
    p = plan([group("g_1", ["a", "b", "c"], 3)])
    live = {("skazka", "a"): with_brand(live_item("Роза", "g_1"), "Сказка", 100),
            ("skazka", "b"): with_brand(live_item("Лилия", "g_1"), "", None),
            ("skazka", "c"): with_brand(live_item("Мак", "g_1"), "Котики Котики", 5)}
    for i, bid in enumerate([100, 100, 200]):                          # кабинет: id 100 у большинства «Сказка», 200 редкий
        live[("skazka", f"x{i}")] = with_brand(live_item("Роза", f"x{i}"), "Сказка", bid)
    r = dry_run(p, live, ASP, brand_map=BM)
    brand = [o for o in r.ops if o.op_type == "set_brand"]
    assert [(o.offer_id, o.from_value, o.to_value, o.to_id) for o in brand] == [("b", "", "Сказка", 100)]
    assert any("чужой бренд" in w and "c" in w for w in r.warnings)
    assert any("несколько брендов" in w for w in r.warnings)             # чужой остался, смесь видна


def test_set_brand_changes_wrong_own_brand_and_clears_mixed_warning():
    g = group("g_1", ["a", "b"], 2)
    g.brand = "Нет бренда"                                              # бренд плана = «Нет бренда» (Анна Мария)
    live = {("skazka", "a"): with_brand(live_item("Роза", "g_1"), "Нет бренда", 126745801),
            ("skazka", "b"): with_brand(live_item("Лилия", "g_1"), "Сказка", 100)}
    r = dry_run(plan([g]), live, ASP, brand_map=BM)                 # «Сказка» в таблице — своя, её можно заменять
    brand = [o for o in r.ops if o.op_type == "set_brand"]
    assert [(o.offer_id, o.from_value, o.to_value, o.from_id, o.to_id) for o in brand] == [("b", "Сказка", "Нет бренда", 100, 126745801)]
    assert not any("несколько брендов" in w for w in r.warnings)


def test_brand_without_known_id_or_mapping_blocks_group():
    live = {("skazka", "a"): with_brand(live_item("Роза", "g_1"), "", None)}
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP, brand_map=BM)           # нет ни одной карточки с id «Сказка»
    assert r.blocked and "нет id бренда" in r.blocked[0].reasons[0]
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP, brand_map={"Другой": "X"})
    assert r.blocked and "не описан" in r.blocked[0].reasons[0]


def test_case_difference_is_not_a_change_and_brand_off_without_map():
    live = {("skazka", "a"): with_brand(live_item("Роза", "g_1"), "СКАЗКА", 9)}
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP, brand_map=BM)
    assert not [o for o in r.ops if o.op_type == "set_brand"]
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP)
    assert not [o for o in r.ops if o.op_type == "set_brand"]
