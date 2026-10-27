import json

import pytest

from recommend import wb_jam
from recommend.wb_jam import Chrome, JamError, upload


class FakeChrome:
    instances: list = []

    def __init__(self, port):
        self.port, self.calls, self.closed = port, [], False
        FakeChrome.instances.append(self)

    def attach(self):
        pass

    def evaluate(self, expression):
        files, _ = json.JSONDecoder().raw_decode(expression.rsplit(")(", 1)[1])
        names = [f["name"] for f in files]
        self.calls.append(names)
        return {"auth": "portal.seller-token|cookie.x-supplier-id", "results": [{"name": n, "task": 1} for n in names]}

    def close(self):
        self.closed = True


def test_upload_batches_files_and_closes_chrome(tmp_path, monkeypatch):
    FakeChrome.instances.clear()
    monkeypatch.setattr(wb_jam, "Chrome", FakeChrome)
    files = []
    for i in range(1, 10):
        f = tmp_path / f"template ({i}).xlsx"
        f.write_bytes(b"x" * 10)
        files.append(f)
    out = upload(9223, files, log=lambda m: None)
    ch = FakeChrome.instances[0]
    assert [len(c) for c in ch.calls] == [4, 4, 1] and ch.closed and ch.port == 9223
    assert len(out["results"]) == 9 and out["auth"].startswith("portal.seller-token")


def test_upload_raises_when_page_has_no_auth(tmp_path, monkeypatch):
    class Bad(FakeChrome):
        def evaluate(self, expression):
            return {"error": "нет подходящих заголовков авторизации (a:401)"}
    monkeypatch.setattr(wb_jam, "Chrome", Bad)
    f = tmp_path / "template (1).xlsx"
    f.write_bytes(b"x")
    with pytest.raises(JamError, match="заголовков"):
        upload(9222, [f], log=lambda m: None)


def test_attach_refuses_when_not_logged_in(monkeypatch):
    class R:
        def __init__(self, data):
            self._d = data

        def json(self):
            return self._d
    tab = {"id": "1", "type": "page", "url": "https://seller-auth.wildberries.ru/ru/?x", "webSocketDebuggerUrl": "ws://x"}
    monkeypatch.setattr(wb_jam.httpx, "get", lambda *a, **k: R([tab]))
    monkeypatch.setattr(wb_jam.httpx, "put", lambda *a, **k: R(tab))        # своя вкладка тоже уходит на страницу входа
    monkeypatch.setattr(wb_jam.time, "sleep", lambda s: None)
    with pytest.raises(JamError, match="нет входа"):
        Chrome(9222).attach()


def test_attach_reports_dead_browser(monkeypatch):
    def boom(*a, **k):
        raise wb_jam.httpx.ConnectError("refused")
    monkeypatch.setattr(wb_jam.httpx, "get", boom)
    with pytest.raises(JamError, match="не отвечает"):
        Chrome(9224).attach()


def _setup(tmp_path, monkeypatch, cur, upload_result):
    import tomllib
    from pathlib import Path

    from recommend.wb import Result
    from tools import wb_recommend as wr

    prof = tomllib.load(open(Path(wr.__file__).resolve().parent.parent / "profiles" / "wb_proftex.toml", "rb"))
    res = Result(recs={1: [(10, "top"), (11, "new")], 2: [(10, "top"), (12, "new")], 3: [(11, "top")]})
    state = {"now": cur}
    monkeypatch.setattr(wr, "read_current", lambda api, nms=None: state["now"])
    monkeypatch.setattr(wr, "upload", lambda port, files, hint="", log=print: upload_result(files))
    monkeypatch.setattr(wr, "time", type("T", (), {"sleep": staticmethod(lambda s: None), "time": staticmethod(lambda: 0)}))
    monkeypatch.setattr(wr, "AUTH_STATE", tmp_path / "state" / "auth.json")
    return wr, prof, res


def test_jam_sync_uploads_only_changed_cards_known_to_wb(tmp_path, monkeypatch):
    seen = {}

    def fake_upload(files):
        seen["n"] = len(files)
        return {"auth": "a|b", "results": [{"name": f.name, "task": 1} for f in files]}
    wr, prof, res = _setup(tmp_path, monkeypatch, {1: [10, 11], 2: [10, 99]}, fake_upload)   # 1 совпала, 2 изменилась, 3 нет на WB
    cur = {1: [10, 11], 2: [10, 99]}
    calls = iter([{2: [10, 12]}])
    monkeypatch.setattr(wr, "read_current", lambda api, nms=None: next(calls))
    out = tmp_path / "out"
    out.mkdir()
    problems = wr.jam_sync("skazka", prof, res, {}, None, out, cur)
    assert problems == [] and seen["n"] == 1
    import openpyxl
    rows = list(openpyxl.load_workbook(out / "skazka_upload" / "template (1).xlsx", read_only=True).active.iter_rows(min_row=2, values_only=True))
    assert {r[0] for r in rows if r[0]} == {2}


def test_jam_sync_reports_file_errors_and_unconfirmed(tmp_path, monkeypatch):
    wr, prof, res = _setup(tmp_path, monkeypatch, {}, lambda files: {"auth": "a|b", "results": [{"name": files[0].name, "error": "upload 500"}]})
    cur = {1: [99], 2: [99]}
    calls = iter([{1: [99], 2: [99]}])
    monkeypatch.setattr(wr, "read_current", lambda api, nms=None: next(calls))
    out = tmp_path / "out"
    out.mkdir()
    problems = wr.jam_sync("skazka", prof, res, {}, None, out, cur)
    assert any("upload 500" in p for p in problems) and any("совпало только 0 из 2" in p for p in problems)
