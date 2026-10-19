from datetime import date

from ops.sync_gate import check

T = date(2026, 10, 20)


def test_fresh():
    ok, why = check(T, T, date(2026, 10, 19), T)
    assert ok and why == []


def test_stale_stocks_blocks():
    ok, why = check(date(2026, 10, 19), T, T, T)
    assert not ok and "остатки Озона" in why[0]


def test_missing_values_block_with_reasons():
    ok, why = check(None, None, None, T)
    assert not ok and len(why) == 3


def test_old_orders_block():
    ok, why = check(T, T, date(2026, 10, 17), T)
    assert not ok and "заказ" in why[0]
