"""Списки стажировок и вакансий для выпускников SimplifyJobs на GitHub (Summer2027-Internships,
New-Grad-Positions). Берём машиночитаемый .github/scripts/listings.json, а не README: в нём компания, роль,
ссылка на ATS, локации и признак «вакансия ещё открыта».

Ссылка ведёт на ATS фирмы. Если это Greenhouse / Lever / Ashby и фирма есть в firms.yaml, вакансия получает
тот же uid, что и при опросе доски фирмы: дублей нет, автопилот подаёт как обычно. Остальное (Workday, iCIMS,
свои сайты) — ats «page»: оценка и письмо будут, откликаться вручную."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from ..jobs import Job
from .http import SourceError, get_json

QUANT = {"Quant", "Quantitative Finance"}


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_firm(company: str, firms: list) -> Any | None:
    """Фирма из firms.yaml по названию из списка: «Five Rings Capital» → Five Rings, «XTX Markets» → XTX Markets."""
    c = _key(company)
    for f in firms:
        k = _key(f.name)
        if k and (c == k or c.startswith(k) or k.startswith(c)):
            return f
    return None


def _clean(url: str) -> str:
    """Без меток utm_* и ref, которые добавляет Simplify."""
    u = urlparse(url)
    q = "&".join(p for p in u.query.split("&") if p and not p.lower().startswith(("utm_", "ref=")))
    return u._replace(query=q).geturl()


def parse(data: Any, firms: list, kind_hint: str) -> list[Job]:
    from . import detect  # здесь, чтобы не было циклического импорта
    if not isinstance(data, list):
        raise SourceError("simplify: listings.json — не список")
    out = []
    for x in data:
        if not (x.get("active") and x.get("is_visible", True) and x.get("category") in QUANT and x.get("url")):
            continue
        url = _clean(x["url"])
        firm = match_firm(x.get("company_name") or "", firms)
        hit = detect(url)
        ats, token, ext_id = hit if hit else ("", "", "")
        if ats and not token and firm is not None and firm.ats == ats:
            token = firm.token  # ссылка без имени доски (…/embed/job_app?token=…, gh_jid на сайте фирмы)
        if not (ats in ("greenhouse", "lever", "ashby") and token and ext_id):
            ats, token, ext_id = "page", urlparse(url).netloc, str(x.get("id") or url)
        posted = x.get("date_posted")
        out.append(Job(
            firm=firm.name if firm is not None else (x.get("company_name") or "").strip(),
            ats=ats, token=token, ext_id=ext_id, title=(x.get("title") or "").strip(),
            location="; ".join(x.get("locations") or []), url=url, apply_url=url,
            published_at=datetime.fromtimestamp(posted, UTC).isoformat() if isinstance(posted, (int, float)) else "",
            hints=[kind_hint, *[t for t in x.get("terms") or [] if t != "N/A"]],
        ))
    return out


def fetch(url: str, firms: list) -> list[Job]:
    hint = "New Grad" if "new-grad" in url.lower() else "Internship"
    return parse(get_json(url, timeout=60), firms, hint)
