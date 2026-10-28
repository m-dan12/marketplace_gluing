"""Рекомендации продавца WB: считает по кабинетам и пишет файлы для загрузки в Джем (только чтение БД, на WB ничего не уходит).

    python -m tools.wb_recommend                        # все кабинеты, файлы в out/recommend/<дата>/
    python -m tools.wb_recommend --accounts skazka --out out/rec
Загрузка: Джем -> подборка «Рекомендации продавца» -> добавить -> через шаблон Excel. Файлы по шаблону WB (шапка, Sheet1): A — артикул WB, к которому рекомендуем
(20 одинаковых строк), B — рекомендуемый артикул WB; до 1000 строк на файл (лимит окна загрузки), режим загрузки «Заменить». Рядом лежит <кабинет>_details.csv: слот, артикулы продавца, названия.

Ежедневный режим --jam: считает, сверяет с текущими рекомендациями на WB (/list, ключ чтения), грузит в Джем только изменившиеся карточки через
Chrome кабинета (порт cdp_port профиля, см. recommend/wb_jam.py), через 2 минуты перепроверяет по /list. Код возврата 1, если кабинет не удалось
обработать (Chrome не отвечает, нет входа в WB, WB не принял файлы); остальные кабинеты при этом всё равно обрабатываются.

Запись через API (токен WB_REC_TOKEN_SK/MG/TL, а если его нет, токен склеек, из MARKETPLACE_MERGE_ENV): --apply; --limit N — только первые N карточек (проба); перед записью текущие
рекомендации сохраняются в <кабинет>_backup.json, вернуть их: --restore <файл> --accounts <кабинет>. Без --apply на WB ничего не пишется.
"""
from __future__ import annotations

import argparse
import builtins
import csv
import json
import shutil
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl
import psycopg

from gluing.brands import brand_for
from gluing.cards import _FABRIC_KEY
from gluing.lists import eco_designs
from gluing.model import Card
from gluing.parsing import parse_article
from gluing.wb.profile import load_profile
from plan_ozon import database_url
from plan_wb import CABINETS
from planner.reader import read_rolls
from applier.ozon_client import read_env
from applier.wb_client import ACCOUNT_ENV, api_for, conf, merge_env_path
from planner.wb_reader import read_imt_members, read_wb_rows
from ops.notify import OpsMonitor
from recommend.report import TITLES, build_report, summary, write_xlsx
from recommend.wb import Result, recommend
from recommend.wb_jam import JamError, upload
from recommend.wb_send import backup, load_backup, read_current, send


def print(*args, **kw) -> None:                                  # noqa: A001  строки лога со временем и сразу на диск (stdout в файле иначе буферизуется)
    builtins.print(datetime.now().strftime("%H:%M:%S"), *args, flush=True, **kw)

TEMPLATE = Path(__file__).resolve().parent.parent / "recommend" / "wb_template.xlsx"


def load_cards(rows: list[dict], rolls: dict[str, int], prof: dict) -> list[Card]:
    """Карточки с остатком и известным брендом; ключ размера не обязателен (у декора и карнавала его нет)."""
    cards = []
    for r in rows:
        art = (r["offer_id"] or "").strip()
        info = parse_article(art)
        brand = brand_for(info["prefix"])
        if brand is None:
            continue
        c = Card(article=art, nm_id=int(r["nm_id"]), title=r.get("title") or "", category=r.get("category") or "", brand=brand,
                 prefix=info["prefix"], design=info["design"], key=info["key"], key_nums=info["key_nums"],
                 stock_wb=int(r.get("stock_wb") or 0), stock_fbs=int(r.get("stock_fbs") or 0), orders30=int(r.get("orders30") or 0))
        if c.category == prof["rules"]["fabric_category"]:
            m = _FABRIC_KEY.match(c.key)
            code = f"{c.prefix}{c.design}"
            if m and code in rolls:
                c.roll_code, c.roll_len_m = code, int(m.group(1))
                c.stock_roll = rolls[code] // c.roll_len_m
        if c.stock_total > 0 and c.design:
            cards.append(c)
    return cards


def write_files(acc: str, cards: dict[int, Card], res, out: Path, rows_per_file: int, total: int, folder: str = "") -> list[Path]:
    """Файлы по шаблону WB (recommend/wb_template.xlsx: Sheet1, шапка «Артикул WB» / «Связанный артикул WB»), имя template (N).xlsx как у WB."""
    per_file = max((rows_per_file - 1) // total, 1)              # карточка целиком в одном файле, шапка входит в лимит строк
    targets = sorted(res.recs)
    paths = []
    (out / (folder or acc)).mkdir(exist_ok=True)
    for i in range(0, len(targets), per_file):
        wb = openpyxl.load_workbook(TEMPLATE)
        ws = wb["Sheet1"]
        row = 2
        for t in targets[i:i + per_file]:
            for nm, _ in res.recs[t]:
                ws.cell(row, 1, t)
                ws.cell(row, 2, nm)
                row += 1
        p = out / (folder or acc) / f"template ({i // per_file + 1}).xlsx"
        wb.save(p)
        paths.append(p)
    if folder:                                                   # файлы на загрузку: подробности ведёт полный расчёт
        return paths
    with open(out / f"{acc}_details.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["nm_id", "артикул", "категория", "рекомендуем nm_id", "артикул", "категория", "слот", "название"])
        for t in targets:
            for nm, slot in res.recs[t]:
                a, b = cards[t], cards[nm]
                w.writerow([t, a.article, a.category, nm, b.article, b.category, slot, b.title])
    return paths


KNOWN_STATE = Path("state") / "wb_rec_known.json"                # {кабинет: {nm_id: дата, когда карточка впервые попала в список рекомендаций WB}}
AUTH_STATE = Path("state") / "wb_jam_auth.json"                  # какой вариант заголовков подошёл в прошлый раз (только имена, без значений)
UNMATCHED_MIN, UNMATCHED_SHARE = 10, 0.2                         # заполнение считается неудачным, если не совпало больше max(10 карточек, 20% загруженных)
VERIFY_WAIT = 120                                               # Джем обрабатывает файлы не мгновенно; потом сверяем по /list


def eligible_ids(acc: str, known: dict[int, list[int]], today: str) -> set[int]:
    """Какие карточки можно рекомендовать: те, что WB уже знал в прошлые дни. Только что оприходованная карточка получает свои рекомендации сразу,
    но как рекомендуемую WB её принимает не сразу (отклоняет), поэтому в чужие рекомендации она попадает со следующего дня."""
    try:
        state = json.loads(KNOWN_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    seen = state.get(acc)
    if seen is None:
        seen = {str(n): "" for n in known}                       # первый запуск: всё, что уже есть в списке, считаем давно известным
    seen = {n: d for n, d in seen.items() if int(n) in known}
    for n in known:
        seen.setdefault(str(n), today)
    try:
        KNOWN_STATE.parent.mkdir(exist_ok=True)
        KNOWN_STATE.write_text(json.dumps({**state, acc: seen}), encoding="utf-8")
    except OSError:
        pass
    return {int(n) for n, d in seen.items() if d < today}


def jam_sync(acc: str, prof: dict, res: Result, cards: dict[int, Card], api, out: Path, cur: dict[int, list[int]]) -> tuple[list[str], dict, dict]:
    """Загружает в Джем кабинета только изменившиеся карточки; возвращает (проблемы, состояние рекомендаций на WB после загрузки, итоги).
    Пустые проблемы = рекомендации заполнены."""
    rc = prof["recommend"]
    new = {t: [nm for nm, _ in v] for t, v in sorted(res.recs.items())}
    backup(out / f"{acc}_backup.json", cur)
    changed = {t: v for t, v in new.items() if v and cur.get(t, []) != v}   # карточки, которых ещё нет в списке WB, тоже грузим: WB их принимает и добавляет в список
    clear = {t: cur[t] for t, v in new.items() if not v and cur.get(t)}                # подобрать нечего, а на WB стоят старые (чужие) рекомендации: убираем
    print(f"{acc}: к загрузке {len(changed)} из {len(new)} карточек (остальные уже совпадают), к очистке {len(clear)}")
    stats = {"changed": len(changed) + len(clear), "matched": 0, "files": 0}
    if not changed and not clear:
        return [], cur, stats
    try:
        hint = json.loads(AUTH_STATE.read_text(encoding="utf-8")).get(acc, "")
    except (OSError, ValueError):
        hint = ""
    problems: list[str] = []
    n_files = 0
    for action, folder, recs in (("replace", f"{acc}_upload", {t: res.recs[t] for t in changed}),
                                 ("delete", f"{acc}_clear", {t: [(r, "") for r in lst] for t, lst in clear.items()})):
        if not recs:
            continue
        files = write_files(acc, cards, Result(recs=recs), out, rc["rows_per_file"], rc["total"], folder=folder)
        try:
            done = upload(rc["cdp_port"], files, action=action, hint=hint, log=lambda m: print(f"{acc}: {m}"))
        except JamError as e:
            return [f"{acc}: {e}"], cur, stats
        hint = done["auth"]
        problems += [f"{acc}: {r['name']}: {r['error']}" for r in done["results"] if r.get("error")]
        n_files += len(files)
    try:
        AUTH_STATE.parent.mkdir(exist_ok=True)
        state = json.loads(AUTH_STATE.read_text(encoding="utf-8")) if AUTH_STATE.exists() else {}
        AUTH_STATE.write_text(json.dumps({**state, acc: hint}), encoding="utf-8")
    except (OSError, ValueError):
        pass
    time.sleep(VERIFY_WAIT)
    now = read_current(api)
    expect = {**changed, **{t: [] for t in clear}}
    ok = sum(1 for t, v in expect.items() if set(now.get(t, [])) == set(v))
    stats.update(matched=ok, files=n_files)
    print(f"{acc}: загружено файлов {n_files}, на WB совпало {ok} из {len(expect)} карточек")
    if len(expect) - ok > max(UNMATCHED_MIN, UNMATCHED_SHARE * len(expect)):
        problems.append(f"{acc}: на WB совпало только {ok} из {len(expect)} карточек после загрузки (смотрите историю загрузок в Джеме)")
    return problems, now, stats


def notice(level: str, title: str, text: str, key: str) -> None:
    """Сообщение в дашборд Ops Monitor (если настроен OPSMON_URL/OPSMON_TOKEN); сбой отправки не должен ронять запуск."""
    print(f"[{level}] {title}", text, sep="\n")
    url, token = conf("OPSMON_URL"), conf("OPSMON_TOKEN")
    if not (url and token):
        return
    try:
        OpsMonitor(url, token, conf("OPSMON_HOST_ID") or "proftex-server", source="рекомендации WB").send(level, title, text, key)
    except Exception as e:                                       # noqa: BLE001
        print(f"дашборд недоступен: {e}")


REPORT_DIR = Path("out") / "recommend"                            # report_<кабинет>.xlsx: только последний успешный запуск


def finish_jam(acc: str, prof: dict, res: Result, cards: list[Card], imt: dict[int, int], api, out: Path, known: dict, eco: frozenset[str]) -> list[str]:
    """Загрузка + отчёт. Прежний отчёт кабинета удаляется сразу: если рекомендации не заполнились, отчёта нет (и в дашборд уходит сообщение об ошибке)."""
    report = REPORT_DIR / f"report_{acc}.xlsx"
    report.unlink(missing_ok=True)
    by_nm = {c.nm_id: c for c in cards}
    problems, actual, stats = jam_sync(acc, prof, res, by_nm, api, out, known)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if problems:
        notice("error", f"Рекомендации WB, {TITLES[acc]}: не заполнены", "\n".join(problems)[:3500], f"wb-rec-{acc}-{stamp}")
        return problems
    rep = build_report(actual, by_nm, imt, prof, eco)
    level, title, text = summary(acc, rep, stats)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    write_xlsx(report, acc, rep, by_nm, text)
    notice(level, title, f"{text}\nПодробности: {report}", f"wb-rec-{acc}-{stamp}")
    return []


def prune(root: Path, keep_days: int = 14) -> None:
    for d in root.iterdir() if root.exists() else []:
        if d.is_dir() and time.time() - d.stat().st_mtime > keep_days * 86400:
            shutil.rmtree(d, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--accounts", nargs="*", default=list(CABINETS), choices=list(CABINETS))
    ap.add_argument("--out", default="")
    ap.add_argument("--apply", action="store_true", help="записать рекомендации на WB через API")
    ap.add_argument("--limit", type=int, default=0, help="с --apply: только первые N карточек")
    ap.add_argument("--jam", action="store_true", help="ежедневный режим: загрузить изменившиеся рекомендации в Джем через Chrome кабинета")
    ap.add_argument("--merge", action="store_true", help="с --apply: добавить к старым рекомендациям, а не заменить")
    ap.add_argument("--restore", default="", help="файл backup.json: вернуть сохранённые рекомендации (один кабинет)")
    a = ap.parse_args()
    out = Path(a.out or Path("out") / "recommend" / a.as_of)
    out.mkdir(parents=True, exist_ok=True)
    env = read_env(conf("MARKETPLACE_ENV"))
    mp = merge_env_path()
    menv = read_env(mp) if mp else {}
    for acc, (_, mk) in ACCOUNT_ENV.items():                     # токен записи рекомендаций (WB_REC_TOKEN_…) важнее токена склеек
        if menv.get(mk.replace("MERGE", "REC")):
            menv[mk] = menv[mk.replace("MERGE", "REC")]
    if a.restore:
        if len(a.accounts) != 1:
            raise SystemExit("--restore: укажите один кабинет в --accounts")
        old = load_backup(Path(a.restore))
        errs = send(api_for(a.accounts[0], env, menv), old, replace=True)
        print(f"восстановлено карточек {len(old)}, ошибок WB {len(errs)}")
        return
    problems: list[str] = []
    if a.jam:
        prune(out.parent)
    with psycopg.connect(database_url()) as conn:
        rolls = read_rolls(conn)
        for acc in a.accounts:
            prof = load_profile(CABINETS[acc])
            rows = read_wb_rows(conn, acc, a.as_of, prof.get("stock", {}).get("wb_mode", "all_physical"))
            cards = load_cards(rows, rolls, prof)
            known: dict[int, list[int]] = {}
            eligible = None
            if a.jam:
                known = read_current(api_for(acc, env, menv))
                eligible = eligible_ids(acc, known, a.as_of)
            imt = {nm: i for i, nms in read_imt_members(conn, acc).items() for nm in nms}
            use = prof["rules"].get("use_lists", True)
            res = recommend(cards, imt, prof, eco_designs() if use else frozenset(), eligible)
            total = prof["recommend"]["total"]
            files = write_files(acc, {c.nm_id: c for c in cards}, res, out, prof["recommend"]["rows_per_file"], total)
            full = sum(len(v) == total for v in res.recs.values())
            slots = Counter(s for v in res.recs.values() for _, s in v)
            print(f"{acc}: карточек с остатком {len(cards)}, с рекомендациями {len(res.recs)}, полных {full}, файлов {len(files)}; "
                  f"слоты {dict(slots)}; не набрано {dict(res.short)}; чаще всего рекомендуем {res.used.most_common(3)}")
            if a.apply:
                api = api_for(acc, env, menv)
                new = {t: [nm for nm, _ in v] for t, v in sorted(res.recs.items())}
                if a.limit:
                    new = dict(list(new.items())[:a.limit])
                cur = read_current(api, set(new))
                backup(out / f"{acc}_backup.json", cur)
                errs = send(api, new, replace=not a.merge)
                print(f"{acc}: записано карточек {len(new)} (до этого с рекомендациями {sum(1 for v in cur.values() if v)}), "
                      f"ошибок WB {len(errs)}: {errs[:3]}")
            if a.jam:
                try:
                    problems += finish_jam(acc, prof, res, cards, imt, api_for(acc, env, menv), out, known, eco_designs() if use else frozenset())
                except Exception as e:                           # noqa: BLE001  один кабинет не должен ронять остальные и молчать
                    msg = f"{acc}: сбой загрузки: {type(e).__name__}: {e}"
                    REPORT_DIR.joinpath(f"report_{acc}.xlsx").unlink(missing_ok=True)
                    notice("error", f"Рекомендации WB, {TITLES[acc]}: не заполнены", msg[:3500], f"wb-rec-{acc}-{datetime.now():%Y%m%d-%H%M}")
                    problems.append(msg)
    if problems:
        print("ПРОБЛЕМЫ:", *problems, sep="\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
