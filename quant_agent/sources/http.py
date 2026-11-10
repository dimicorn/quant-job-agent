"""HTTP без браузера: публичные API досок отвечают JSON без логина и капчи."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

USER_AGENT = "quant-job-agent/0.1"


class SourceError(Exception):
    """Доска не ответила или ответила не тем. Отличается от «0 вакансий»: это разные события в здоровье источников."""


def get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"{type(e).__name__}: {e} ({url})")


def post_json(url: str, payload: Any, timeout: float = 30) -> Any:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"User-Agent": USER_AGENT, "Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} {url}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceError(f"{type(e).__name__}: {e} ({url})")
    except ValueError:
        raise SourceError(f"не JSON: {url}")


def get_json(url: str, timeout: float = 30) -> Any:
    raw = get(url, timeout)
    try:
        return json.loads(raw)
    except ValueError:
        raise SourceError(f"не JSON: {url}")
