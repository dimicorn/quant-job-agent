"""Свой сайт фирмы (Alber Blanc, Wunder Fund, QST, Quantum Brains…): ATS нет, есть страница карьеры.

token — адрес страницы карьеры. links — регулярное выражение для путей страниц вакансий на ней
(«^/jobs/[^/]+$»): каждая такая страница разбирается parse_page (JSON-LD JobPosting или текст).
Без links вся страница карьеры — одна «вакансия», её id — хэш текста: поменялся список позиций —
появилась новая запись, и она снова попадёт на оценку. Откликаться на таких сайтах — вручную."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlparse

from ..jobs import Job, html_to_text
from .http import SourceError, get

_HREF = re.compile(r"""href\s*=\s*["']([^"'#]+)["']""", re.I)
MAX_PAGES = 40  # больше страниц вакансий за опрос не открываем


def _text(html: str) -> str:
    return html_to_text(re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", "", html, flags=re.S | re.I))


def job_links(html: str, base: str, links: str) -> list[str]:
    """Ссылки на вакансии со страницы карьеры: тот же сайт, путь подходит под links, без повторов."""
    rx, host, seen = re.compile(links), urlparse(base).netloc, []
    for href in _HREF.findall(html):
        u = urljoin(base, href.strip())
        p = urlparse(u)
        if p.netloc == host and rx.search(p.path) and u.rstrip("/") != base.rstrip("/") and u not in seen:
            seen.append(u)
    return seen[:MAX_PAGES]


def parse_listing(html: str, firm: str, url: str) -> Job:
    """Вся страница карьеры как одна запись: id — хэш текста."""
    text = _text(html)
    digest = hashlib.sha1(text.encode()).hexdigest()[:12]
    return Job(firm=firm, ats="site", token=url, ext_id=f"page-{digest}", title=f"{firm}: открытые позиции",
               url=url, apply_url=url, description=text[:20000])


def fetch(firm: str, token: str, links: str = "") -> list[Job]:
    from . import parse_page  # здесь, чтобы не было циклического импорта
    html = get(token).decode("utf-8", "replace")
    if not links:
        return [parse_listing(html, firm, token)]
    urls = job_links(html, token, links)
    if not urls:
        raise SourceError(f"site/{firm}: на {token} нет ссылок под шаблон {links!r} — сайт поменялся?")
    out = []
    for u in urls:
        try:
            job = parse_page(get(u).decode("utf-8", "replace"), u)
        except SourceError:
            continue  # одна битая страница не роняет опрос фирмы
        job.firm, job.ats, job.token, job.ext_id = firm, "site", token, urlparse(u).path.rstrip("/")
        if job.title.strip().lower() in ("", u.lower()):
            job.title = urlparse(u).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        out.append(job)
    return out


def fetch_one(firm: str, token: str, ext_id: str) -> Job:
    from . import parse_page
    u = urljoin(token, ext_id)
    job = parse_page(get(u).decode("utf-8", "replace"), u)
    job.firm, job.ats, job.token, job.ext_id = firm, "site", token, ext_id
    return job
