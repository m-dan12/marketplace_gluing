"""Загрузка рекомендаций в Джем WB через уже открытый Chrome кабинета (CDP), как у автоответов на отзывы (marketplaces_auto_replies).

На proftex-server на каждый кабинет постоянно работает свой Chrome (--remote-debugging-port, профиль C:\\ChromeProfiles\\<кабинет>), в нём человек один раз
входит в seller.wildberries.ru. Публичный API рекомендаций закрыт тарифом Джем, поэтому делаем то же, что страница «Рекомендации продавца»:
два запроса на seller-content.wildberries.ru (upload-recoms -> fileID, setMassRecommendations). Запросы идут ИЗНУТРИ страницы (Runtime.evaluate):
токен сессии читается из localStorage страницы и в Python, логи и файлы не попадает, куки браузер подставляет сам.

Как и в автоответах: чужие вкладки не трогаем; вкладка seller.wildberries.ru берётся любая из открытых, а если её нет, открывается своя и потом закрывается.
Нет браузера на порту, нет входа в кабинет, не подошли заголовки: JamError (починка руками, код её не обходит)."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import websocket

SELLER = "https://seller.wildberries.ru/"
PAGE = SELLER + "recommendations-v3"
BASE = "https://seller-content.wildberries.ru/ns/"
HISTORY = BASE + "suppliers-recom/content-card/api/v1/getUploadHistory"
UPLOAD = BASE + "upload-manager/content-card/api/v1/upload-recoms"
SET_MASS = BASE + "suppliers-recom/content-card/api/v1/setMassRecommendations"
BATCH = 4                                   # файлов за один вызов страницы (вызов ждёт ответов WB)


class JamError(RuntimeError):
    pass


# Выполняется в странице. Заголовки страницы: AuthorizeV3 (токен кабинета), Seller-Lk, root-version. Откуда страница их берёт, подбираем
# безопасным запросом истории загрузок: 401/403 — не подошло, любой другой код — заголовки приняты. Удачный вариант возвращаем (только имена).
PAGE_JS = r"""
async (files, action, hint) => {
  const ls = k => { try { return localStorage.getItem(k) } catch (e) { return null } };
  const ck = n => { const m = document.cookie.split(';').map(s => s.trim()).find(s => s.startsWith(n + '=')); return m ? decodeURIComponent(m.slice(n.length + 1)) : null };
  const clean = v => { if (!v) return null; v = String(v).trim(); try { const j = JSON.parse(v); if (typeof j === 'string') v = j; else if (j && typeof j === 'object') v = j.access_token || j.token || j.accessToken || v } catch (e) {} return v.replace(/^Bearer\s+/i, '') };
  const tokens = {'portal.seller-token': clean(ls('portal.seller-token')), 'passport.access-token': clean(ls('wb-eu-passport-v2.access-token'))};
  const lks = {'cookie.x-supplier-id': ck('x-supplier-id'), 'cookie.x-supplier-id-external': ck('x-supplier-id-external'),
               'ls.PROMOTION_SUPPLIER_ID': clean(ls('PROMOTION_SUPPLIER_ID_STORAGE_KEY')), 'ls.seller-token-user-id': ls('portal.seller-token-user-id')};
  const rv = ls('@root/latest-app-version') || '';
  const combos = [];
  for (const [tn, t] of Object.entries(tokens)) for (const [ln, l] of Object.entries(lks)) if (t && l) combos.push({name: tn + '|' + ln, t, l});
  combos.sort((a, b) => (b.name === hint) - (a.name === hint));
  const mk = c => ({'AuthorizeV3': c.t, 'Seller-Lk': c.l, 'root-version': rv});
  let used = null, probes = [];
  for (const c of combos) {
    const r = await fetch('%HISTORY%', {method: 'POST', headers: {...mk(c), 'Content-type': 'application/json'}, body: '{}', credentials: 'include'});
    probes.push(c.name + ':' + r.status);
    if (r.status !== 401 && r.status !== 403) { used = c; break }
  }
  if (!used) return {error: 'нет подходящих заголовков авторизации (' + probes.join(', ') + ')', url: location.href};
  const out = [];
  for (const f of files) {
    const r = {name: f.name};
    try {
      const bin = atob(f.b64), u8 = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
      const fd = new FormData();
      fd.append('file', new File([u8], f.name, {type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));
      const a = await fetch('%UPLOAD%', {method: 'POST', headers: mk(used), body: fd, credentials: 'include'});
      const aj = await a.json().catch(() => ({}));
      if (a.status !== 200 || aj.error || !aj.data) { r.error = 'upload ' + a.status + ' ' + (aj.errorText || ''); out.push(r); continue }
      const b = await fetch('%SET%', {method: 'POST', headers: {...mk(used), 'Content-type': 'application/json'},
                                       body: JSON.stringify({fileID: aj.data, name: f.name, action}), credentials: 'include'});
      const bj = await b.json().catch(() => ({}));
      if (b.status !== 200 || bj.error) { r.error = 'set ' + b.status + ' ' + (bj.errorText || ''); out.push(r); continue }
      r.task = bj.data && bj.data.taskID;
    } catch (e) { r.error = String(e).slice(0, 200) }
    out.push(r);
    await new Promise(s => setTimeout(s, 1500));
  }
  return {auth: used.name, results: out};
}
""".replace("%HISTORY%", HISTORY).replace("%UPLOAD%", UPLOAD).replace("%SET%", SET_MASS)


class Chrome:
    """Один Chrome кабинета: вкладка seller.wildberries.ru и вызовы Runtime.evaluate."""

    def __init__(self, port: int):
        self.port = port
        self.http = f"http://localhost:{port}"
        self.opened: str | None = None        # id вкладки, которую открыли мы (её и закроем)
        self.ws = None
        self._id = 0

    def _targets(self) -> list[dict]:
        try:
            return httpx.get(f"{self.http}/json", timeout=5.0).json()
        except httpx.HTTPError as e:
            raise JamError(f"Chrome на порту {self.port} не отвечает: {e}") from e

    def attach(self) -> None:
        pages = [t for t in self._targets() if t.get("type") == "page"]
        tab = next((t for t in pages if t.get("url", "").startswith(SELLER)), None)
        if tab is None:
            try:
                tab = httpx.put(f"{self.http}/json/new?{PAGE}", timeout=10.0).json()
            except httpx.HTTPError as e:
                raise JamError(f"не удалось открыть вкладку в Chrome на порту {self.port}: {e}") from e
            self.opened = tab["id"]
            time.sleep(8)                                          # загрузка страницы кабинета и редирект на вход, если нет сессии
            tab = next((t for t in self._targets() if t.get("id") == self.opened), tab)
        if "seller-auth" in tab.get("url", "") or not tab.get("url", "").startswith(SELLER):
            raise JamError(f"Chrome на порту {self.port}: нет входа в кабинет WB (вкладка на {tab.get('url', '')[:60]})")
        self.ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=300, suppress_origin=True)

    def evaluate(self, expression: str):
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": "Runtime.evaluate", "params": {
            "expression": expression, "awaitPromise": True, "returnByValue": True, "timeout": 280000}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                break
        res = msg.get("result", {})
        if "exceptionDetails" in res or "error" in msg:
            raise JamError("ошибка страницы: " + str(res.get("exceptionDetails", msg.get("error")))[:300])
        return res["result"].get("value")

    def close(self) -> None:
        if self.ws:
            self.ws.close()
        if self.opened:
            try:
                httpx.get(f"{self.http}/json/close/{self.opened}", timeout=5.0)
            except httpx.HTTPError:
                pass


def upload(port: int, files: list[Path], action: str = "replace", hint: str = "", log=print) -> dict:
    """Загружает файлы в Джем (режим action: replace | add | delete). Возвращает {"auth": вариант заголовков, "results": [...]}; ошибки по файлам — в results[].error."""
    chrome = Chrome(port)
    results: list[dict] = []
    auth = ""
    try:
        chrome.attach()
        for i in range(0, len(files), BATCH):
            part = [{"name": f.name, "b64": base64.b64encode(f.read_bytes()).decode()} for f in files[i:i + BATCH]]
            out = chrome.evaluate(f"({PAGE_JS})({json.dumps(part)}, {json.dumps(action)}, {json.dumps(hint)})")
            if not isinstance(out, dict) or out.get("error"):
                raise JamError((out or {}).get("error", "пустой ответ страницы") if isinstance(out, dict) else "пустой ответ страницы")
            auth = out["auth"]
            hint = auth
            results += out["results"]
            log(f"загружено файлов {len(results)} из {len(files)}")
    finally:
        chrome.close()
    return {"auth": auth, "results": results}
