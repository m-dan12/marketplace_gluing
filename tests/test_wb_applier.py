import pytest

from applier.executor import MemoryJournal
from applier.wb_executor import WbSettings, execute_wb, rollback_wb
from applier.wb_ops import dry_run_wb
from contract import Design, Group, Member, Plan, RunInfo

ST = WbSettings(sleep=lambda s: None)


class FakeWb:
    """WB в памяти: {nm_id: imtID}. Соблюдает лимит 30; move без target отделяет карточки поодиночке."""

    def __init__(self, state, fail_after=None, ignore=()):
        self.state = dict(state)
        self.next_imt = 9000
        self.moves = 0
        self.fail_after, self.ignore = fail_after, set(ignore)

    def imts(self, account, cards):
        return {n: self.state[n] for _, n in cards if n in self.state}

    def move(self, account, nm_ids, target):
        self.moves += 1
        if self.fail_after is not None and self.moves > self.fail_after:
            raise RuntimeError("HTTP 500")
        if target is None:
            for n in nm_ids:
                self.next_imt += 1
                self.state[n] = self.next_imt
            return
        if sum(1 for v in self.state.values() if v == target) + len([n for n in nm_ids if self.state[n] != target]) > 30:
            raise RuntimeError("в склейке больше 30 карточек")
        for n in nm_ids:
            if n not in self.ignore:
                self.state[n] = target


def group(gid, nms, imt=None, acc="skazka"):
    return Group(group_id=gid, key=gid, account=acc, brand="B", type="Наволочки", branch="Ключ", kind="glue",
                 target={"wb": {"imt_id": imt}},
                 designs=[Design(design=str(i), members=[Member(offer_id=f"A{n}", platform_ids={"nm_id": n})]) for i, n in enumerate(nms)])


def plan(*groups):
    return Plan(run=RunInfo(run_id="r", platform="wb", created_at="x", engine_version="t", profile_name="p", profile_hash="h"),
                groups=list(groups))


def live_of(w):
    return {("skazka", n): i for n, i in w.state.items()}


def apply(w, pl, st=ST, j=None):
    j = j or MemoryJournal()
    res = dry_run_wb(pl, live_of(w))
    out = execute_wb("r", res.ops, {"skazka": w}, j, st, res.sizes)
    return res, out, j


def test_simple_move_and_idempotent_rerun():
    w = FakeWb({1: 10, 2: 10, 3: 20, 4: 20})
    pl = plan(group("g", [1, 2, 3], 10))
    res, out, j = apply(w, pl)
    assert out["result"] == {"done": 1} and w.state[3] == 10
    again = dry_run_wb(pl, live_of(w))
    assert again.ops == [] and again.already_ok == 3                      # повторный запуск ничего не делает


def test_new_group_created_from_seed():
    w = FakeWb({1: 10, 9: 10, 2: 11, 8: 11, 3: 12, 7: 12})               # у каждой карточки склейка с чужими: нужна новая
    pl = plan(group("g", [1, 2, 3], None))
    res, out, j = apply(w, pl)
    assert out["result"] == {"done": 3}
    assert len({w.state[n] for n in (1, 2, 3)}) == 1 and w.state[1] not in (10, 11, 12)
    assert dry_run_wb(pl, live_of(w)).ops == []


def test_group_reuses_pure_imt_instead_of_splitting():
    w = FakeWb({1: 10, 9: 10, 2: 11, 3: 12})                              # склейки 11 и 12 содержат только карточки группы
    res, out, j = apply(w, plan(group("g", [1, 2, 3], None)))
    assert out["result"] == {"done": 2} and w.moves == 2 + 0
    assert w.state[1] == w.state[2] == w.state[3]


def test_crash_midway_then_rerun_does_only_remainder(monkeypatch):
    import applier.wb_executor as ex
    monkeypatch.setattr(ex, "BATCH", 5)
    state = {n: 100 for n in range(1, 15)} | {n: 101 for n in range(15, 29)} | {n: 101 for n in range(29, 41)}
    w = FakeWb(state)
    pl = plan(group("g", list(range(1, 29)), 100))
    res = dry_run_wb(pl, live_of(w))
    first = len(res.ops)
    assert first == 14
    w.fail_after = 1                                                       # первый пакет пройдёт, второй упадёт
    out = execute_wb("r", res.ops, {"skazka": w}, MemoryJournal(), WbSettings(sleep=lambda s: None, probe=False), res.sizes)
    assert out["result"].get("failed") and out["result"].get("done")
    w.fail_after = None
    res2 = dry_run_wb(pl, live_of(w))
    assert 0 < len(res2.ops) < first
    execute_wb("r2", res2.ops, {"skazka": w}, MemoryJournal(), ST, res2.sizes)
    assert all(w.state[n] == 100 for n in range(1, 29))
    assert dry_run_wb(pl, live_of(w)).ops == []


def test_capacity_order_moves_leavers_first():
    # склейка 10 заполнена на 30: 5 карточек уходят в 20, 5 карточек из 20 приходят в 10
    state = {n: 10 for n in range(1, 31)} | {n: 20 for n in range(101, 111)}
    w = FakeWb(state)
    pl = plan(group("a", list(range(6, 31)) + list(range(101, 106)), 10), group("b", list(range(1, 6)) + list(range(106, 111)), 20))
    res, out, j = apply(w, pl)
    assert out["result"].get("done") == len(res.ops) and not out.get("stopped")
    assert all(w.state[n] == 10 for n in list(range(6, 31)) + list(range(101, 106)))
    assert all(w.state[n] == 20 for n in list(range(1, 6)) + list(range(106, 111)))


def test_full_swap_cycle_is_broken_by_split():
    state = {n: 10 for n in range(1, 31)} | {n: 20 for n in range(101, 131)}
    w = FakeWb(state)
    a = [n for n in range(2, 31)] + [101]
    b = [n for n in range(102, 131)] + [1]
    res, out, j = apply(w, plan(group("a", a, 10), group("b", b, 20)))
    assert not out.get("stopped"), out
    assert all(w.state[n] == 10 for n in a) and all(w.state[n] == 20 for n in b)


def test_stale_card_is_skipped():
    w = FakeWb({1: 10, 2: 10, 3: 20})
    pl = plan(group("g", [1, 2, 3], 10))
    res = dry_run_wb(pl, live_of(w))
    w.state[3] = 30                                                        # после плана карточку перенесли руками
    out = execute_wb("r", res.ops, {"skazka": w}, MemoryJournal(), ST, res.sizes)
    assert out["result"] == {"skipped": 1} and w.state[3] == 30


def test_probe_failure_stops_everything():
    w = FakeWb({1: 10, 2: 20, 3: 20}, fail_after=0)
    res = dry_run_wb(plan(group("g", [1, 2, 3], 10)), live_of(w))
    out = execute_wb("r", res.ops, {"skazka": w}, MemoryJournal(), ST, res.sizes)
    assert out["stopped"] == "probe_failed"


def test_verification_catches_silent_ignore():
    w = FakeWb({1: 10, 2: 20}, ignore={2})
    res = dry_run_wb(plan(group("g", [1, 2], 10)), live_of(w))
    out = execute_wb("r", res.ops, {"skazka": w}, MemoryJournal(), WbSettings(sleep=lambda s: None, probe=False), res.sizes)
    assert out["result"] == {"failed": 1}


def test_blocked_group_when_inactive_cards_fill_the_imt():
    state = {n: 10 for n in range(1, 31)} | {200: 20}
    w = FakeWb(state)
    res = dry_run_wb(plan(group("g", [1, 200], 10)), live_of(w))           # в склейке 10 ещё 29 карточек вне плана
    assert res.blocked and not res.ops


def test_missing_live_card_blocks_group():
    w = FakeWb({1: 10})
    res = dry_run_wb(plan(group("g", [1, 2], 10)), live_of(w))
    assert res.blocked and "нет живой карточки" in res.blocked[0].reasons[0]


def test_rollback_restores_old_groups():
    w = FakeWb({1: 10, 2: 10, 3: 20, 4: 20})
    pl = plan(group("g", [1, 2, 3, 4], 10))
    res, out, j = apply(w, pl)
    nm = {("skazka", f"A{n}"): n for n in (1, 2, 3, 4)}
    r = rollback_wb("r", {"skazka": w}, j, nm, ST)
    assert r["result"] == {"reverted": 2}
    assert w.state[3] == 20 and w.state[4] == 20
