from applier.executor import MemoryJournal, Settings, execute, rollback
from applier.ops import BRAND_ATTR, COLOR_NAME_ATTR, MODEL_NAME_ATTR, Op


class FakeOzon:
    """Атрибуты карточек в памяти; задача обработки всегда завершается сразу."""

    def __init__(self, cards, fail=(), ignore=()):
        self.cards = cards                              # {offer_id: {attr_id: value}}
        self.fail, self.ignore = set(fail), set(ignore)  # fail: Озон отклоняет, ignore: «принял», но значение не меняет
        self.tasks, self.calls = {}, []

    def post(self, path, body):
        self.calls.append(path)
        if path == "/v4/product/info/attributes":
            offers = body["filter"]["offer_id"]
            return {"result": [{"offer_id": o, "attributes": [{"id": a, "values": [{"value": v}]} for a, v in self.cards[o].items()]}
                               for o in offers if o in self.cards]}
        if path == "/v1/product/attributes/update":
            tid = len(self.tasks) + 1
            items = []
            for it in body["items"]:
                o, a = it["offer_id"], it["attributes"][0]
                v = a["values"][0]["value"]
                if o in self.fail:
                    items.append({"offer_id": o, "status": "failed", "errors": [{"message": "отказ"}]})
                else:
                    if o not in self.ignore:
                        self.cards[o][a["id"]] = v
                    items.append({"offer_id": o, "status": "imported", "errors": []})
            self.tasks[tid] = items
            return {"task_id": tid}
        if path == "/v1/product/import/info":
            return {"result": {"items": self.tasks[body["task_id"]]}}
        raise AssertionError(path)


def ops_for(cards, typ="set_model_name", attr=MODEL_NAME_ATTR, to="M1"):
    return [Op("skazka", o, typ, attr, c.get(attr, ""), to, "g", None) for o, c in cards.items()]


ST = Settings(sleep=lambda s: None)


def cards(n, **extra):
    return {f"o{i}": {MODEL_NAME_ATTR: "old", COLOR_NAME_ATTR: "Бисер", **extra} for i in range(n)}


def test_execute_writes_verifies_and_journals():
    api, j = FakeOzon(cards(3)), MemoryJournal()
    r = execute("r", ops_for(api.cards), {"skazka": api}, j, ST)
    assert r["result"] == {"done": 3}
    assert all(c[MODEL_NAME_ATTR] == "M1" for c in api.cards.values())
    assert {x["status"] for x in j.rows("r")} == {"done"}


def test_stale_value_is_skipped_not_overwritten():
    api, j = FakeOzon(cards(2)), MemoryJournal()
    ops = ops_for(api.cards)
    api.cards["o1"][MODEL_NAME_ATTR] = "changed-by-someone"        # после плана значение поменяли руками
    r = execute("r", ops, {"skazka": api}, j, ST)
    assert r["result"] == {"done": 1, "skipped": 1}
    assert api.cards["o1"][MODEL_NAME_ATTR] == "changed-by-someone"


def test_colors_go_before_model_names():
    api, j = FakeOzon(cards(2)), MemoryJournal()
    ops = ops_for(api.cards, "set_model_name") + ops_for(api.cards, "set_color_name", COLOR_NAME_ATTR, "Бисер 2")
    execute("r", ops, {"skazka": api}, j, Settings(probe=False, sleep=lambda s: None))
    types = [r["op_type"] for r in sorted(j.rows("r"), key=lambda r: r["op_id"])]
    first_writes = [c for c in api.calls if c == "/v1/product/attributes/update"]
    assert len(first_writes) == 2 and types.count("set_color_name") == 2


def test_probe_failure_stops_everything():
    api, j = FakeOzon(cards(3), fail={"o0"}), MemoryJournal()
    r = execute("r", ops_for(api.cards), {"skazka": api}, j, ST)
    assert r["stopped"] == "probe_failed"
    assert [x["status"] for x in j.rows("r")] == ["failed", "skipped", "skipped"]
    assert all(c[MODEL_NAME_ATTR] == "old" for c in api.cards.values())


def test_verification_mismatch_is_failed():
    api, j = FakeOzon(cards(2), ignore={"o1"}), MemoryJournal()
    r = execute("r", ops_for(api.cards), {"skazka": api}, j, ST)
    assert r["result"] == {"done": 1, "failed": 1}
    assert "сверка" in j.rows("r", "failed")[0]["error"]


def test_max_ops_guard_writes_nothing():
    api, j = FakeOzon(cards(5)), MemoryJournal()
    r = execute("r", ops_for(api.cards), {"skazka": api}, j, Settings(max_ops=3, sleep=lambda s: None))
    assert r["stopped"] == "max_ops" and not j.rows("r") and "/v1/product/attributes/update" not in api.calls


def test_rollback_restores_previous_values():
    api, j = FakeOzon(cards(3)), MemoryJournal()
    execute("r", ops_for(api.cards), {"skazka": api}, j, ST)
    r = rollback("r", {"skazka": api}, j, ST)
    assert r["result"] == {"done": 3}
    assert all(c[MODEL_NAME_ATTR] == "old" for c in api.cards.values())
    assert {x["status"] for x in j.rows("r")} == {"reverted"}


def test_rollback_reports_empty_previous_values():
    api, j = FakeOzon({"o0": {MODEL_NAME_ATTR: ""}}), MemoryJournal()
    execute("r", ops_for(api.cards), {"skazka": api}, j, ST)
    assert rollback("r", {"skazka": api}, j, ST)["очистить вручную (прежнее значение пустое)"] == ["o0"]


class SpyOzon(FakeOzon):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent = []

    def post(self, path, body):
        if path == "/v1/product/attributes/update":
            self.sent.append(body)
        return super().post(path, body)


def brand_ops(offers, frm="Сказка", to="Нет бренда", fid=100, tid=126745801):
    return [Op("skazka", o, "set_brand", BRAND_ATTR, frm, to, "g", None, fid, tid) for o in offers]


def test_brand_written_with_dictionary_id_before_other_ops_and_rolled_back_with_old_id():
    api = SpyOzon({"o1": {BRAND_ATTR: "Сказка", MODEL_NAME_ATTR: "old"}})
    j = MemoryJournal()
    ops = [Op("skazka", "o1", "set_model_name", MODEL_NAME_ATTR, "old", "M1", "g")] + brand_ops(["o1"])
    out = execute("r", ops, {"skazka": api}, j, ST)
    assert out["result"].get("done") == 2
    first = api.sent[0]["items"][0]["attributes"][0]
    assert first["id"] == BRAND_ATTR and first["values"] == [{"dictionary_value_id": 126745801, "value": "Нет бренда"}]
    assert api.cards["o1"][BRAND_ATTR] == "Нет бренда"
    rollback("r", {"skazka": api}, j, ST)
    back = [b for b in api.sent if b["items"][0]["attributes"][0]["id"] == BRAND_ATTR][-1]["items"][0]["attributes"][0]["values"]
    assert back == [{"dictionary_value_id": 100, "value": "Сказка"}] and api.cards["o1"][BRAND_ATTR] == "Сказка"


def test_rollback_of_brand_without_old_id_goes_to_manual_list():
    api = SpyOzon({"o1": {BRAND_ATTR: ""}})
    j = MemoryJournal()
    execute("r", brand_ops(["o1"], frm="", fid=None), {"skazka": api}, j, ST)
    out = rollback("r", {"skazka": api}, j, ST)
    assert out["очистить вручную (прежнее значение пустое)"] == ["o1"]
