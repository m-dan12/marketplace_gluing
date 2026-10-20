"""Живое состояние карточек WB -> gluing.wb_card (только чтение с WB, запись в нашу схему)."""
from __future__ import annotations

from applier.wb_client import WbApi, list_cards


def rows_from_cards(account: str, cards: list[dict]) -> list[tuple]:
    return [(account, int(c["nmID"]), c.get("vendorCode") or "", int(c["imtID"]), c.get("subjectName"), c.get("brand"), c.get("title"))
            for c in cards if c.get("nmID") and c.get("imtID")]


def refresh_wb_cards(conn, account: str, api: WbApi, cards: list[dict] | None = None) -> dict:
    """Перечитывает все карточки кабинета и заменяет gluing.wb_card одной транзакцией. Возвращает счётчики."""
    cards = cards if cards is not None else list_cards(api)
    rows = rows_from_cards(account, cards)
    if not rows:
        raise RuntimeError(f"{account}: WB вернул ноль карточек, таблицу не трогаю")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("delete from gluing.wb_card where account=%s", (account,))
        cur.executemany("insert into gluing.wb_card(account,nm_id,vendor_code,imt_id,subject,brand,title) values (%s,%s,%s,%s,%s,%s,%s)", rows)
    return {"account": account, "cards": len(rows), "imt": len({r[3] for r in rows})}
