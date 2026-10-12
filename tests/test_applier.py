from contract import Check, Design, Group, Member, Plan, RunInfo
from applier.ops import COLOR_NAME_ATTR, MODEL_NAME_ATTR, dry_run

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


def test_target_name_taken_by_foreign_offers_blocks_group():
    p = plan([group("g_1", ["a"], 1)])
    live = {("skazka", "a"): live_item("Роза"), ("skazka", "z"): live_item("Лилия", "g_1")}
    r = dry_run(p, live, ASP)
    assert r.blocked and "чужих" in r.blocked[0].reasons[0]
