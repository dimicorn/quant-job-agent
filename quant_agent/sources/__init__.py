"""Источники вакансий. У каждой ATS — модуль с parse() (чистый разбор ответа API, тестируется на фикстурах),
fetch() (вся доска фирмы) и fetch_one() (одна вакансия, для `add <url>`). Площадка выбирается по адресу."""
from __future__ import annotations

import json
import re
from types import ModuleType
from urllib.parse import parse_qs, urlparse

from ..jobs import Job, html_to_text
from . import ashby, greenhouse, lever, site, workable
from .http import SourceError, get

SOURCES: dict[str, ModuleType] = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
                                   "site": site}
API_ATS = ("greenhouse", "lever", "ashby", "workable")  # площадки с API вакансий и формой, которую заполняет агент


def fetch_firm(f) -> list[Job]:
    """Все вакансии фирмы из firms.yaml (у своего сайта есть ещё шаблон ссылок links)."""
    if f.ats == "site":
        return site.fetch(f.name, f.token, f.links)
    return SOURCES[f.ats].fetch(f.name, f.token)


def detect(url: str) -> tuple[str, str, str] | None:
    """(ats, token, ext_id) по ссылке на вакансию или None, если площадка не из SOURCES.
    Greenhouse бывает и на своём домене фирмы — тогда id в параметре gh_jid, а токен неизвестен."""
    u = urlparse(url.strip())
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    if host.endswith("greenhouse.io") and len(parts) >= 3 and parts[-2] == "jobs":
        return "greenhouse", parts[-3], parts[-1]
    qs = parse_qs(u.query)
    if host.endswith("greenhouse.io") and parts and parts[-1] == "job_app" and qs.get("token"):
        return "greenhouse", (qs.get("for") or [""])[0], qs["token"][0]  # форма-вставка; без for доска неизвестна
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(parts) >= 2:
        return "lever", parts[0], parts[1]
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return "ashby", parts[0], parts[1]
    if host == "apply.workable.com" and "j" in parts and parts.index("j") + 1 < len(parts):
        i = parts.index("j")  # /<account>/j/<shortcode>/… или /j/<shortcode> (без аккаунта)
        return "workable", parts[i - 1] if i else "", parts[i + 1]
    if host.endswith(".workable.com") and host != "apply.workable.com" and len(parts) >= 2 and parts[0] == "j":
        return "workable", host.split(".")[0], parts[1]  # старый адрес <account>.workable.com/j/<shortcode>
    gh = qs.get("gh_jid")
    if gh:
        return "greenhouse", "", gh[0]
    return None


# ── Любая страница: JSON-LD JobPosting → текст страницы ─────────────

_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)


def _postings(node):
    if isinstance(node, list):
        for x in node:
            yield from _postings(x)
    elif isinstance(node, dict):
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            yield node
        for k in ("@graph", "itemListElement"):
            if k in node:
                yield from _postings(node[k])


def _location(p: dict) -> str:
    out = []
    locs = p.get("jobLocation") or []
    for loc in locs if isinstance(locs, list) else [locs]:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, dict):
            parts = (addr.get("addressLocality"), addr.get("addressCountry"))
            out.append(", ".join(x for x in parts if isinstance(x, str) and x))
    return "; ".join(x for x in out if x)


def parse_page(page: str, url: str) -> Job:
    """Вакансия со страницы фирмы. Сначала разметка JobPosting (её ставят ради Google Jobs), иначе — заголовок
    и текст страницы."""
    host = urlparse(url).netloc
    for raw in _LD.findall(page):
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        for p in _postings(data):
            org = p.get("hiringOrganization")
            ident = p.get("identifier")
            emp = p.get("employmentType")
            return Job(firm=(org.get("name") if isinstance(org, dict) else "") or host, ats="page", token=host,
                       ext_id=str(ident.get("value")) if isinstance(ident, dict) and ident.get("value") else url,
                       title=html_to_text(p.get("title") or ""), location=_location(p), url=url, apply_url=url,
                       description=html_to_text(p.get("description") or ""), published_at=p.get("datePosted") or "",
                       hints=[emp] if isinstance(emp, str) else [x for x in emp or [] if isinstance(x, str)])
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S | re.I)
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", page, flags=re.S | re.I)
    return Job(firm=host, ats="page", token=host, ext_id=url, title=html_to_text(m.group(1)) if m else url,
               url=url, apply_url=url, description=html_to_text(body)[:20000])


def fetch_url(url: str, firm: str = "") -> Job:
    """Одна вакансия по ссылке: через API ATS, если площадка известна, иначе со страницы."""
    hit = detect(url)
    if hit and hit[1]:
        ats, token, ext_id = hit
        return SOURCES[ats].fetch_one(firm or token, token, ext_id)
    job = parse_page(get(url).decode("utf-8", "replace"), url)
    if firm:
        job.firm = firm
    return job


__all__ = ["API_ATS", "SOURCES", "SourceError", "detect", "fetch_firm", "fetch_url", "parse_page"]
