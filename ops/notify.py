"""Уведомления из шины: в дашборд Ops Monitor и (когда появится бот) в Яндекс Мессенджер.

Подписчик notify читает события gluing.<ozon|wb>.apply_started / applied / apply_blocked и ops.alert, переводит их в короткий текст и
отправляет в подключённые каналы. Настройки в .env проекта (значения в чат и в логи не попадают).

Дашборд Ops Monitor (основной канал, пока нет бота):
    OPSMON_URL         адрес сервера монитора, например http://100.74.113.18:8750
    OPSMON_TOKEN       токен агента этого хоста (тот же, что у агента Ops Monitor на proftex-server)
    OPSMON_HOST_ID     имя хоста в мониторе (по умолчанию proftex-server)
Яндекс Мессенджер (бот организации Яндекс 360):
    YANDEX_BOT_TOKEN   токен бота (админка Яндекс 360, раздел «Боты»)
    YANDEX_CHAT_ID     идентификатор группового чата (бот должен быть его участником), либо
    YANDEX_LOGIN       логин сотрудника для личных сообщений
Пока не подключён ни один канал, тексты пишутся в logs/notify.log, а события считаются обработанными."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://botapi.messenger.yandex.net/bot/v1/messages/sendText/"
TOPICS = ["gluing.ozon.apply_started", "gluing.ozon.applied", "gluing.ozon.apply_blocked",
          "gluing.wb.apply_started", "gluing.wb.applied", "gluing.wb.apply_blocked", "ops.alert"]
log = logging.getLogger("notify")


def render(topic: str, p: dict) -> str | None:
    """Текст сообщения по событию; None — событие не для уведомления."""
    run = p.get("run_id", "")
    wb = ".wb." in topic
    topic = topic.replace(".wb.", ".ozon.")                      # тексты одни, меняется только площадка
    plat, shop = ("WB", "WB") if wb else ("Озона", "Озон")
    if topic == "gluing.ozon.apply_started":
        by = ", ".join(f"{k}: {v}" for k, v in (p.get("summary", {}).get("по типам") or {}).items())
        return (f"Склейки {plat}: запускаю применение плана {run}.\nОпераций: {p.get('ops')}" + (f" ({by})" if by else "") + ".\n"
                f"Проверки пройдены, предупреждений: {len(p.get('warns') or [])}.")
    if topic == "gluing.ozon.applied":
        if not p.get("ops"):
            return f"Склейки {plat}: изменений не нужно, все склейки сходятся (план {run}, артикулов {p.get('members', '')})."
        r = p.get("result") or {}
        bad = r.get("failed", 0) + r.get("skipped", 0)
        head = "применён полностью" if not bad and not p.get("stopped") else "применён с замечаниями"
        lines = [f"Склейки {plat}: план {run} {head}.", f"Выполнено: {r.get('done', 0)} из {p.get('ops')}."]
        if bad:
            lines.append(f"Не прошло: {bad} (журнал gluing.op, статус failed/skipped).")
        if r.get("слетело после повторной сверки"):
            lines.append(f"Слетело при повторной сверке: {r['слетело после повторной сверки']}.")
        if p.get("stopped"):
            lines.append(f"Остановлено: {p['stopped']}.")
        return "\n".join(lines)
    if topic == "gluing.ozon.apply_blocked":
        why = "\n".join(f"- {x}" for x in (p.get("reasons") or [])[:8])
        return f"Склейки {plat}: автоприменение плана {run} остановлено, на {shop} ничего не записано.\nПричины:\n{why}"
    if topic == "ops.alert":
        return f"Склейки: {p.get('text', 'проблема')}"
    return None


def classify(topic: str, p: dict) -> tuple[str, str]:
    """(уровень, заголовок) для дашборда: info / ok / warn / error. Текст сообщения даёт render()."""
    plat = "WB" if ".wb." in topic else "Озона"
    topic = topic.replace(".wb.", ".ozon.")
    if topic == "gluing.ozon.apply_started":
        return "info", f"Склейки {plat}: запущено применение"
    if topic == "gluing.ozon.applied":
        if not p.get("ops"):
            return "ok", f"Склейки {plat}: изменений не нужно"
        r = p.get("result") or {}
        if r.get("failed", 0) + r.get("skipped", 0) or p.get("stopped"):
            return "warn", f"Склейки {plat}: применено с замечаниями"
        return "ok", f"Склейки {plat}: применено полностью"
    if topic == "gluing.ozon.apply_blocked":
        return "error", f"Склейки {plat}: автоприменение остановлено"
    return "warn", ("Склейки: " + str(p.get("text", "проблема цепочки")).split(":")[0])[:120]


class OpsMonitor:
    """Канал в дашборд Ops Monitor: POST /api/agent/notice с токеном агента хоста. key делает отправку идемпотентной."""

    def __init__(self, url: str, token: str, host_id: str = "proftex-server", source: str = "склейки", client=None):
        if not url or not token:
            raise ValueError("нужен OPSMON_URL и OPSMON_TOKEN")
        self.url, self.token, self.host_id, self.source, self._client = url.rstrip("/"), token, host_id, source, client

    def send(self, level: str, title: str, text: str, key: str, ts: int | None = None) -> None:
        import httpx
        body = {"host_id": self.host_id, "source": self.source, "key": key, "level": level, "title": title[:200], "text": text[:4000]}
        if ts:
            body["ts"] = ts
        r = (self._client or httpx).post(f"{self.url}/api/agent/notice?host_id={self.host_id}", json=body,
                                         headers={"Authorization": f"Bearer {self.token}"}, timeout=15)
        if r.status_code >= 400:
            raise RuntimeError(f"Ops Monitor: HTTP {r.status_code} {str(r.text)[:200]}")


class Yandex:
    def __init__(self, token: str, chat_id: str = "", login: str = "", client=None):
        if not token or not (chat_id or login):
            raise ValueError("нужен токен и chat_id или login")
        self.token, self.chat_id, self.login, self._client = token, chat_id, login, client

    def send(self, text: str, payload_id: str | None = None) -> int | None:
        import httpx
        body = {"text": text[:6000], "disable_web_page_preview": True}
        body["chat_id" if self.chat_id else "login"] = self.chat_id or self.login
        if payload_id:
            body["payload_id"] = payload_id                      # повторная отправка того же события не создаст дубль
        r = (self._client or httpx).post(URL, json=body, headers={"Authorization": f"OAuth {self.token}"}, timeout=30)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or not data.get("ok"):
            raise RuntimeError(f"Яндекс Мессенджер: HTTP {r.status_code} {data.get('description', r.text[:200])}")
        return data.get("message_id")


def from_conf(conf) -> Yandex | None:
    token, chat, login = conf("YANDEX_BOT_TOKEN"), conf("YANDEX_CHAT_ID"), conf("YANDEX_LOGIN")
    return Yandex(token, chat, login) if token and (chat or login) else None


def ops_from_conf(conf) -> OpsMonitor | None:
    url, token = conf("OPSMON_URL"), conf("OPSMON_TOKEN")
    return OpsMonitor(url, token, conf("OPSMON_HOST_ID") or "proftex-server") if url and token else None


def make_handler(client: Yandex | None, log_file: Path | None = None, ops: OpsMonitor | None = None):
    """Обработчик событий для Router. Ошибка отправки поднимается наверх: шина повторит с паузой (в мониторе дубль исключён ключом
    события, в мессенджере — payload_id)."""
    def h(ev):
        text = render(ev.topic, ev.payload)
        if not text:
            return
        if ops is not None:
            level, title = classify(ev.topic, ev.payload)
            ops.send(level, title, text, key=f"bus-{ev.id}", ts=int(ev.created_at.timestamp()) if getattr(ev, "created_at", None) else None)
        if client is None and ops is not None:
            return                                                   # дашборд подключён: лог-файл нужен только как запасной канал
        if client is None:
            line = f"{datetime.now().isoformat(timespec='seconds')} [{ev.topic}] {text}\n"
            if log_file:
                log_file.parent.mkdir(exist_ok=True)
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(line)
            log.warning("бот Яндекс Мессенджера не настроен, сообщение записано в лог: %s", text.splitlines()[0])
            return
        client.send(text, payload_id=f"bus-{ev.id}")
    return h
