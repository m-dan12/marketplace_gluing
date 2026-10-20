"""Бренд в разных категориях (85 / 31), «Нет бренда» == пусто, повторная сверка исполнителя."""
from applier.executor import MemoryJournal, Settings, execute
from applier.ops import COLOR_NAME_ATTR, MODEL_NAME_ATTR, Op, dry_run
from planner.ozon_attrs import brand_id_of
from tests.test_applier import ASP, group, live_item, plan
from tests.test_executor import FakeOzon, SpyOzon

BM = {"Сказка": "Сказка", "Нет бренда": "Нет бренда"}


def card(brand_attr, text="", bid=None, model="g_1"):
    item = live_item("Роза", model)
    item = {**item, "attrs": dict(item["attrs"]), "brand_attr": brand_attr, "brand_id": bid}
    if text:
        item["attrs"][str(brand_attr)] = [text]
    return item


def test_clothes_category_reads_brand_from_attr_31_and_needs_no_op_when_equal():
    live = {("skazka", "a"): card(31, "Сказка", 971218860)}
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP, brand_map=BM)
    assert not [o for o in r.ops if o.op_type == "set_brand"] and not r.warnings


def test_set_brand_in_clothes_writes_attr_31_with_majority_id():
    live = {("skazka", "a"): card(31, "", None), ("skazka", "x"): card(31, "Сказка", 971218860, model="x"),
            ("skazka", "y"): card(31, "Сказка", 971218860, model="y")}
    r = dry_run(plan([group("g_1", ["a"], 1)]), live, ASP, brand_map=BM)
    ops = [o for o in r.ops if o.op_type == "set_brand"]
    assert [(o.offer_id, o.attr_id, o.to_id) for o in ops] == [("a", 31, 971218860)]


def test_empty_brand_equals_no_brand_no_operation():
    g = group("g_1", ["a", "b"], 2)
    g.brand = "Нет бренда"
    live = {("skazka", "a"): card(85, "Нет бренда", 126745801), ("skazka", "b"): card(85, "", None)}
    r = dry_run(plan([g]), live, ASP, brand_map=BM)
    assert not [o for o in r.ops if o.op_type == "set_brand"] and not r.warnings


def test_brand_id_of_reads_85_or_31():
    assert brand_id_of({"attributes": [{"id": 85, "values": [{"dictionary_value_id": 5, "value": "Сказка"}]}]}) == 5
    assert brand_id_of({"attributes": [{"id": 31, "values": [{"dictionary_value_id": 7, "value": "Сказка"}]}]}) == 7
    assert brand_id_of({"attributes": [{"id": 31, "values": []}]}) is None
    assert brand_id_of({"attributes": []}) is None


def test_execute_groups_by_attribute_so_85_and_31_are_written_to_the_right_attribute():
    api = SpyOzon({"o1": {85: "", MODEL_NAME_ATTR: "m"}, "o2": {31: "", MODEL_NAME_ATTR: "m"}})
    ops = [Op("skazka", "o1", "set_brand", 85, "", "Сказка", "g", None, None, 1),
           Op("skazka", "o2", "set_brand", 31, "", "Сказка", "g", None, None, 1)]
    out = execute("r", ops, {"skazka": api}, MemoryJournal(), Settings(sleep=lambda s: None, recheck=0))
    assert out["result"].get("done") == 2
    sent = {b["items"][0]["offer_id"]: b["items"][0]["attributes"][0]["id"] for b in api.sent}
    assert sent == {"o1": 85, "o2": 31}


class RevertingOzon(FakeOzon):
    """Принимает запись и показывает новое значение, но при следующем перечитывании возвращает старое (как делал Озон с брендом)."""

    def __init__(self, cards):
        super().__init__(cards)
        self.old, self.reads_after_write = {}, {}

    def post(self, path, body):
        if path == "/v1/product/attributes/update":
            for it in body["items"]:
                a = it["attributes"][0]["id"]
                self.old[(it["offer_id"], a)] = self.cards[it["offer_id"]].get(a, "")
                self.reads_after_write[it["offer_id"]] = 0
        if path == "/v4/product/info/attributes":
            for o in body["filter"]["offer_id"]:
                if o in self.reads_after_write:
                    self.reads_after_write[o] += 1
                    if self.reads_after_write[o] >= 2:                 # вторая проверка после записи уже видит откат
                        for (off, a), v in self.old.items():
                            if off == o:
                                self.cards[o][a] = v
        return super().post(path, body)


def test_recheck_marks_values_that_reverted_after_a_pause():
    api = RevertingOzon({"o1": {85: "", MODEL_NAME_ATTR: "m"}})
    j = MemoryJournal()
    ops = [Op("skazka", "o1", "set_brand", 85, "", "Сказка", "g", None, None, 1)]
    out = execute("r", ops, {"skazka": api}, j, Settings(sleep=lambda s: None, recheck=120, probe=False))
    r = out["result"]
    assert r.get("done", 0) == 0 and r["failed"] == 1 and r["слетело после повторной сверки"] == 1
    assert [x["status"] for x in j.rows("r")] == ["failed"] and "не закрепилось" in j.rows("r")[0]["error"]


class LateOzon(FakeOzon):
    """Принимает запись, но применяет её позже первой сверки (как Озон со снятием с модели): первое перечитывание видит старое."""

    def __init__(self, cards):
        super().__init__(cards)
        self.pending, self.reads = {}, {}

    def post(self, path, body):
        if path == "/v1/product/attributes/update":
            for it in body["items"]:
                a = it["attributes"][0]
                self.pending[it["offer_id"]] = (a["id"], a["values"][0]["value"])
                self.reads[it["offer_id"]] = 0
            res = {"task_id": len(self.tasks) + 1}
            self.tasks[res["task_id"]] = [{"offer_id": it["offer_id"], "status": "imported", "errors": []} for it in body["items"]]
            return res
        if path == "/v4/product/info/attributes":
            for o in body["filter"]["offer_id"]:
                if o in self.pending:
                    self.reads[o] += 1
                    if self.reads[o] >= 2:                              # со второго перечитывания значение применено
                        a, v = self.pending.pop(o)
                        self.cards[o][a] = v
        return super().post(path, body)


def test_recheck_promotes_late_applied_values_to_done():
    api = LateOzon({"o1": {MODEL_NAME_ATTR: "g_old"}})
    j = MemoryJournal()
    ops = [Op("skazka", "o1", "unglue_model", MODEL_NAME_ATTR, "g_old", "o1", "")]
    out = execute("r", ops, {"skazka": api}, j, Settings(sleep=lambda s: None, recheck=120, probe=False))
    r = out["result"]
    assert r["done"] == 1 and r.get("failed", 0) == 0 and r["дозакрепилось при повторной сверке"] == 1
    assert [x["status"] for x in j.rows("r")] == ["done"]


class SkippingOzon(FakeOzon):
    """Как Озон со «временными» значениями: прямая запись цели в такую карточку получает skipped и ничего не меняет,
    а после записи любого другого значения следующая запись проходит."""

    def __init__(self, cards, stuck):
        super().__init__(cards)
        self.stuck = set(stuck)

    def post(self, path, body):
        if path == "/v1/product/attributes/update":
            tid = len(self.tasks) + 1
            items = []
            for it in body["items"]:
                o, a = it["offer_id"], it["attributes"][0]
                v = a["values"][0]["value"]
                if o in self.stuck and not v.endswith("_tmp"):
                    items.append({"offer_id": o, "status": "skipped", "errors": []})
                else:
                    self.cards[o][a["id"]] = v
                    self.stuck.discard(o)
                    items.append({"offer_id": o, "status": "imported", "errors": []})
            self.tasks[tid] = items
            return {"task_id": tid}
        return super().post(path, body)


def test_skipped_write_is_retried_through_a_temporary_value():
    api = SkippingOzon({"o1": {MODEL_NAME_ATTR: "g_x_o1"}, "o2": {MODEL_NAME_ATTR: "old"}}, stuck=["o1"])
    j = MemoryJournal()
    ops = [Op("skazka", "o1", "set_model_name", MODEL_NAME_ATTR, "g_x_o1", "g_x", "g"),
           Op("skazka", "o2", "set_model_name", MODEL_NAME_ATTR, "old", "g_x", "g")]
    out = execute("r", ops, {"skazka": api}, j, Settings(sleep=lambda s: None, recheck=0, probe=False))
    assert out["result"] == {"done": 2}
    assert api.cards["o1"][MODEL_NAME_ATTR] == "g_x" and api.cards["o2"][MODEL_NAME_ATTR] == "g_x"


def test_temporary_value_workaround_can_be_disabled():
    api = SkippingOzon({"o1": {MODEL_NAME_ATTR: "g_x_o1"}}, stuck=["o1"])
    ops = [Op("skazka", "o1", "set_model_name", MODEL_NAME_ATTR, "g_x_o1", "g_x", "g")]
    out = execute("r", ops, {"skazka": api}, MemoryJournal(), Settings(sleep=lambda s: None, recheck=0, probe=False, temp_value=False))
    assert out["result"] == {"failed": 1} and api.cards["o1"][MODEL_NAME_ATTR] == "g_x_o1"
