"""Слежение за досками фирм: новые вакансии → тип → фильтры → условия допуска → оценка LLM → уведомление.
Вакансия пропала с доски — closed (стажировки раздают по мере поступления заявок, закрытые не оцениваем).
Ошибка доски и «0 вакансий» — разные события: ошибка пишется в здоровье источников и не закрывает вакансии."""
from __future__ import annotations

import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import analyst, stop
from .config import Config
from .db import DB, now
from .events import log_event
from .firms import Firm, load_firms
from .jobs import Job, classify_kind, eligibility, prefilter
from .log import console
from .sources import SOURCES, SourceError, fetch_firm, parse_page, simplify
from .sources.http import get

MAX_SEPARATE = 3  # больше новых подходящих вакансий за опрос — одно сводное уведомление


@dataclass
class Poll:
    firm: Firm
    jobs: list[Job] = field(default_factory=list)
    error: str = ""


@dataclass
class Summary:
    boards: int = 0
    errors: int = 0
    new: int = 0
    filtered: int = 0
    ineligible: int = 0
    closed: int = 0
    scored: int = 0
    notified: int = 0


def fetch_all(firms: list[Firm], workers: int) -> list[Poll]:
    def one(f: Firm) -> Poll:
        try:
            return Poll(f, fetch_firm(f))
        except SourceError as e:
            return Poll(f, error=str(e))

    with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="board") as ex:
        return list(ex.map(one, firms))


def ingest(cfg: Config, db: DB, job: Job, kinds: list | None = None) -> str | None:
    """Сохранить вакансию с доски. Новая — вернуть её статус (new | filtered | ineligible), известная — None."""
    kind = classify_kind(job.title, job.hints)
    fields = dict(firm=job.firm, ats=job.ats, token=job.token, ext_id=job.ext_id, title=job.title,
                  location=job.location, kind=kind, url=job.url, apply_url=job.apply_url, department=job.department,
                  description=job.description, compensation=job.compensation, published_at=job.published_at)
    if not db.seen(job.uid, **fields):
        return None
    reason = prefilter(job, kind, cfg.filters, kinds)
    status = "filtered"
    if not reason:
        reason = eligibility(job, kind, cfg.eligibility)
        status = "ineligible" if reason else "new"
    if reason:
        db.update(job.uid, status=status, filter_reason=reason)
    return status


def notify(title: str, text: str) -> None:
    """Уведомление macOS со звуком (на других системах молча пропускается)."""
    safe = lambda s: s.replace("\\", " ").replace('"', "'")[:200]
    try:
        subprocess.run(["osascript", "-e", f'display notification "{safe(text)}" with title "{safe(title)}" sound name "Glass"'],
                       check=False, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _details(job: Job) -> bool:
    """Дочитать текст вакансии из списка-агрегатора. False — вакансия уже закрыта на доске фирмы."""
    try:
        if job.ats in ("greenhouse", "lever", "ashby"):
            full = SOURCES[job.ats].fetch_one(job.firm, job.token, job.ext_id)
            job.description, job.department = full.description, full.department
            job.compensation = job.compensation or full.compensation
        else:
            job.description = parse_page(get(job.url).decode("utf-8", "replace"), job.url).description
    except SourceError:
        return job.ats not in ("greenhouse", "lever", "ashby")  # своя страница не открылась — оценим по названию
    return True


def _aggregators(cfg: Config, db: DB, firms: list[Firm], s: Summary) -> None:
    """Списки SimplifyJobs: новые вакансии, которых ещё нет в базе (в том числе из фирм вне firms.yaml)."""
    known = {f.name: f for f in firms}
    for url in cfg.watch.aggregators:
        name = "Simplify " + url.split("/SimplifyJobs/")[-1].split("/")[0]
        try:
            jobs = simplify.fetch(url, firms)
        except SourceError as e:
            s.errors += 1
            db.log_health(name, "simplify", ok=False, error=str(e))
            console.print(f"[red]✗ {name}[/]: {e}")
            continue
        db.log_health(name, "simplify", ok=True, count=len(jobs))
        budget = cfg.watch.aggregator_details
        for job in jobs:
            stop.check()
            if db.get(job.uid):
                continue
            firm = known.get(job.firm)
            kinds = firm.kinds if firm else None
            if not prefilter(job, classify_kind(job.title, job.hints), cfg.filters, kinds):
                if budget <= 0:
                    continue  # дочитаем в следующий опрос
                budget -= 1
                if not _details(job):
                    continue
            st = ingest(cfg, db, job, kinds)
            if st == "new":
                s.new += 1
            elif st == "filtered":
                s.filtered += 1
            elif st == "ineligible":
                s.ineligible += 1


def run_once(cfg: Config, db: DB, firms: list[Firm] | None = None, score: bool = True) -> Summary:
    firms = firms if firms is not None else load_firms(cfg)
    s = Summary(boards=len(firms))
    for p in fetch_all(firms, cfg.watch.workers):
        stop.check()
        f = p.firm
        if p.error:
            s.errors += 1
            db.log_health(f.name, f.ats, ok=False, error=p.error)
            console.print(f"[red]✗ {f.name}[/]: {p.error}")
            continue
        db.log_health(f.name, f.ats, ok=True, count=len(p.jobs))
        for job in p.jobs:
            st = ingest(cfg, db, job, p.firm.kinds)
            if st == "new":
                s.new += 1
            elif st == "filtered":
                s.filtered += 1
            elif st == "ineligible":
                s.ineligible += 1
        s.closed += db.close_missing(f.name, f.ats, f.token, {j.uid for j in p.jobs})
    _aggregators(cfg, db, firms, s)
    console.print(f"Доски: {s.boards} (ошибок {s.errors}); новых к оценке: {s.new}, отсеяно фильтрами: {s.filtered}, "
                  f"не подходят по условиям: {s.ineligible}, закрылось: {s.closed}")
    if score and s.new:
        s.scored = analyst.analyze_new(cfg, db)
    if cfg.watch.notify:
        rows = db.to_notify(cfg.analyst.min_score)
        if len(rows) > MAX_SEPARATE:  # первый прогон или большой набор — одно сводное уведомление, а не десятки
            top = rows[0]
            notify(f"Новых подходящих вакансий: {len(rows)}",
                   f"Лучшая: {top['firm']} — {top['title']} ({top['score']}). Смотреть: quant-agent review")
        else:
            for row in rows:
                notify(f"{row['firm']}: {row['score']}", f"{row['title']} — {row['location']}")
        for row in rows:
            db.update(row["uid"], notified_at=now())
        s.notified = len(rows)
    log_event(cfg, "watch", **s.__dict__)
    return s


def loop(cfg: Config, db: DB, score: bool = True) -> None:
    """watch без --once: опрос каждые watch.every_minutes, пока не нажмут Ctrl+C."""
    while not stop.requested():
        try:
            run_once(cfg, db, score=score)
        except stop.Stopped:
            break
        console.print(f"Следующий опрос через {cfg.watch.every_minutes} мин. Ctrl+C — выход.")
        if stop.wait(cfg.watch.every_minutes * 60):
            break
