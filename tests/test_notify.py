import pytest

from busclient import Event
from ops.notify import Yandex, make_handler, render

from datetime import datetime, timezone


def ev(topic, payload, i=7):
    return Event(i, topic, "k", payload, "t", None, datetime.now(timezone.utc))


def test_render_applied_variants():
    assert "изменений не нужно" in render("gluing.ozon.applied", {"run_id": "r", "ops": 0, "members": 5378})
    ok = render("gluing.ozon.applied", {"run_id": "r", "ops": 100, "result": {"done": 100}})
    assert "применён полностью" in ok and "100 из 100" in ok
    bad = render("gluing.ozon.applied", {"run_id": "r", "ops": 100, "result": {"done": 90, "failed": 10, "слетело после повторной сверки": 2}})
    assert "с замечаниями" in bad and "Не прошло: 10" in bad and "Слетело при повторной сверке: 2" in bad


def test_render_started_blocked_alert_and_unknown():
    s = render("gluing.ozon.apply_started", {"run_id": "r", "ops": 3, "summary": {"по типам": {"set_model_name": 3}}, "warns": []})
    assert "запускаю применение" in s and "set_model_name: 3" in s
    b = render("gluing.ozon.apply_blocked", {"run_id": "r", "reasons": ["жёсткие нарушения: 2"]})
    assert "остановлено" in b and "- жёсткие нарушения: 2" in b and "ничего не записано" in b
    assert render("ops.alert", {"text": "воркер молчит"}) == "Склейки: воркер молчит"
    assert render("gluing.ozon.dryrun_ready", {}) is None


class FakeHttp:
    def __init__(self, status=200, body=None):
        self.status, self.body, self.sent = status, body if body is not None else {"ok": True, "message_id": 5}, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append((url, json, headers))
        outer = self

        class R:
            status_code = outer.status
            headers = {"content-type": "application/json"}
            text = ""

            def json(self):
                return outer.body
        return R()


def test_yandex_sends_to_chat_with_oauth_header_and_payload_id():
    http = FakeHttp()
    h = make_handler(Yandex("tok", chat_id="0/0/abc", client=http))
    h(ev("ops.alert", {"text": "тест"}))
    url, body, headers = http.sent[0]
    assert url.endswith("/bot/v1/messages/sendText/") and headers["Authorization"] == "OAuth tok"
    assert body["chat_id"] == "0/0/abc" and "login" not in body and body["payload_id"] == "bus-7" and "тест" in body["text"]


def test_yandex_login_target_and_error_is_raised_for_retry():
    http = FakeHttp()
    Yandex("tok", login="user", client=http).send("x")
    assert http.sent[0][1]["login"] == "user" and "chat_id" not in http.sent[0][1]
    bad = Yandex("tok", chat_id="c", client=FakeHttp(403, {"ok": False, "description": "Bot is not a member of the chat"}))
    with pytest.raises(RuntimeError, match="not a member"):
        make_handler(bad)(ev("ops.alert", {"text": "x"}))


def test_without_credentials_writes_log_and_does_not_fail(tmp_path):
    f = tmp_path / "notify.log"
    make_handler(None, f)(ev("gluing.ozon.applied", {"run_id": "r", "ops": 0}))
    assert "изменений не нужно" in f.read_text(encoding="utf-8")
    make_handler(None, f)(ev("gluing.ozon.dryrun_ready", {}))          # не для уведомления: молча пропускаем
    with pytest.raises(ValueError):
        Yandex("", chat_id="")


def test_wb_texts_name_the_platform():
    from ops.notify import TOPICS, render
    assert "gluing.wb.applied" in TOPICS
    t = render("gluing.wb.applied", {"run_id": "r", "ops": 10, "result": {"done": 10}})
    assert "Склейки WB" in t and "Озон" not in t
    b = render("gluing.wb.apply_blocked", {"run_id": "r", "reasons": ["токен склеек WB: нет"]})
    assert "на WB ничего не записано" in b and "токен склеек" in b
    s = render("gluing.wb.apply_started", {"run_id": "r", "ops": 5, "summary": {}, "warns": []})
    assert "Склейки WB" in s and "()" not in s
    assert "Склейки Озона" in render("gluing.ozon.applied", {"run_id": "r", "ops": 1, "result": {"done": 1}})
