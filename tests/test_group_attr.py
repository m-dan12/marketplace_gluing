"""Группирующая характеристика зависит от категории: 9048 «Название модели», у одежды 8292 «Объединить на одной карточке»."""
from applier.ops import COLOR_NAME_ATTR, MERGE_ATTR, MODEL_NAME_ATTR, dry_run
from planner.twins import live_models
from tests.test_applier import ASP, group, plan


def card(group_attr, model="", stock_cat=(1, 2), color="Роза"):
    attrs = {str(COLOR_NAME_ATTR): [color]}
    if model:
        attrs[str(group_attr)] = [model]
    return {"cat": stock_cat, "attrs": attrs, "product_id": 1, "group_attr": group_attr}


def test_clothes_category_writes_group_name_to_8292():
    live = {("skazka", "a"): card(MERGE_ATTR, "Накидка"), ("skazka", "b"): card(MODEL_NAME_ATTR, "old", color="Лилия")}
    g = group("g_1", ["a", "b"], 2)
    r = dry_run(plan([g]), {k: {**v, "cat": (1, 2)} for k, v in live.items()}, ASP)
    ops = {o.offer_id: (o.op_type, o.attr_id, o.from_value, o.to_value) for o in r.ops}
    assert ops["a"] == ("set_model_name", MERGE_ATTR, "Накидка", "g_1")
    assert ops["b"] == ("set_model_name", MODEL_NAME_ATTR, "old", "g_1")


def test_value_already_in_8292_means_no_operation():
    live = {("skazka", "a"): card(MERGE_ATTR, "g_1")}
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP)
    assert not r.ops and r.already_ok == 1


def test_unglue_uses_the_category_group_attribute():
    live = {("skazka", "a"): card(MODEL_NAME_ATTR, "g_1"), ("skazka", "z"): card(MERGE_ATTR, "g_1")}
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP, stock={("skazka", "a"): 5, ("skazka", "z"): 0})
    un = [o for o in r.ops if o.op_type == "unglue_model"]
    assert [(o.offer_id, o.attr_id, o.to_value) for o in un] == [("z", MERGE_ATTR, "z")]


def test_live_models_read_from_the_right_attribute():
    attrs = {("skazka", "a"): card(MERGE_ATTR, "g_7"), ("skazka", "b"): card(MODEL_NAME_ATTR, "g_8")}
    assert live_models(attrs) == {"a": "g_7", "b": "g_8"}
