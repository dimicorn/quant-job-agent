"""HTTP без браузера: публичные API досок отвечают JSON без логина и капчи.
Обрыв соединения, таймаут, 429 и 5xx — повтор с паузой (RETRIES): сеть через VPN иногда мигает,
и без повтора доска на этот опрос считается сломанной."""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Any

from .. import stop

USER_AGENT = "quant-job-agent/0.1"
RETRIES = (2, 6)  # паузы перед повторами, секунды
NETWORK = (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError)


class SourceError(Exception):
    """Доска не ответила или ответила не тем. Отличается от «0 вакансий»: это разные события в здоровье источников."""


def _transient(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code == 429 or e.code >= 500
    return isinstance(e, NETWORK)


def _open(req: urllib.request.Request, timeout: float) -> bytes:
    url = req.full_url
    for delay in (*RETRIES, None):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except NETWORK as e:
            if delay is None or not _transient(e) or stop.wait(delay):
                if isinstance(e, urllib.error.HTTPError):
                    raise SourceError(f"HTTP {e.code} {url}")
                raise SourceError(f"{type(e).__name__}: {e} ({url})")
    raise AssertionError("недостижимо")


def get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html"})
    return _open(req, timeout)


def post_json(url: str, payload: Any, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        return json.loads(_open(req, timeout))
    except ValueError:
        raise SourceError(f"не JSON: {url}")


def get_json(url: str, timeout: float = 30) -> Any:
    raw = get(url, timeout)
    try:
        return json.loads(raw)
    except ValueError:
        raise SourceError(f"не JSON: {url}")
