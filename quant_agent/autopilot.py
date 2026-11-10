"""Автопилот: опрос досок → оценка → письма → план ответов → заполнение формы Greenhouse → (Submit).

Один проход (run_once) и цикл (loop) с опросом досок раз в watch.every_minutes. Ограничения:
  - формы Greenhouse, Lever и Ashby (APPLIERS), балл ≥ apply.min_score, есть письмо;
  - не больше одной заявки в фирму за проход и не больше max_applications за cooldown_months;
  - apply.daily_limit в сутки, apply.max_per_run за запуск, рабочие часы apply.active_hours;
  - вопросы анкеты без ответа → needs_answers: отвечаете один раз (quant-agent answers), ответ запоминается.
Режим (apply.mode): assist — Submit жмёте вы; auto — агент; dry_run — без отправки."""
from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

from . import answers, materials, stop, watch
from .applier import FormError, GreenhouseApplier
from .applier_ashby import AshbyApplier
from .applier_lever import LeverApplier
from .config import Config
from .db import DB, now
from .events import log_event
from .firms import Firm, load_firms
from .log import console
from .sources import SourceError, ashby, greenhouse, lever

# площадка → (модуль с questions() и form_url(), класс заполнения формы)
APPLIERS = {"greenhouse": (greenhouse, GreenhouseApplier), "lever": (lever, LeverApplier),
            "ashby": (ashby, AshbyApplier)}


def in_hours(cfg: Config, t: datetime | None = None) -> bool:
    t = t or datetime.now()
    a, b = cfg.apply.active_hours
    return a <= t.hour < b and not (cfg.apply.weekdays_only and t.weekday() >= 5)


def candidates(cfg: Config, db: DB, firms: list[Firm]) -> list[dict]:
    """Лучшая подходящая вакансия каждой фирмы, где лимит заявок ещё не исчерпан. По tier, потом по баллу."""
    by_name = {f.name: f for f in firms}
    rows = db.for_review(cfg.apply.min_score, {f.name: f.tier for f in firms})
    out, seen = [], set()
    for r in rows:
        f = by_name.get(r["firm"])
        if r["ats"] not in APPLIERS or not r.get("letter") or r["firm"] in seen or f is None:
            continue
        if db.applications(f.name, f.cooldown_months) >= f.max_applications:
            continue
        seen.add(r["firm"])
        out.append(r)
    return out


def prepare(cfg: Config, db: DB, row: dict) -> answers.Plan | None:
    """План ответов; есть вопросы для человека — needs_answers и None."""
    fields = APPLIERS[row["ats"]][0].questions(row["token"], row["ext_id"])
    plan = answers.plan(cfg, row, fields)
    db.update(row["uid"], plan=plan.to_json())
    if plan.missing:
        db.update(row["uid"], status="needs_answers",
                  error="; ".join(f"{m['label'][:80]} ({m['why']})" for m in plan.missing))
        console.print(f"[yellow]? {row['firm']} — {row['title']}[/]: вопросов без ответа — {len(plan.missing)} "
                      f"(uv run quant-agent answers)")
        return None
    return plan


def apply_one(cfg: Config, db: DB, page, row: dict, plan: answers.Plan, mode: str | None = None) -> str:
    title = f"{row['firm']} — {row['title']}"
    console.print(f"Заполняю: {title} ({row['score']})")
    source, cls = APPLIERS[row["ats"]]
    ap = cls(page, cfg, mode)
    files = Path(cfg.path("materials/tmp"))
    files.mkdir(parents=True, exist_ok=True)
    try:
        ap.open(source.form_url(row["token"], row["ext_id"]))
        ap.fill(plan, files)
        problems = ap.verify(plan)
        if problems:
            raise FormError("; ".join(problems))
        status, note = ap.submit(title)
    except FormError as e:
        status, note = "manual", f"форма: {e}; снимок: {ap.screenshot(title)}"
    if status == "applied":
        db.update(row["uid"], status="applied", applied_at=now(), note=note, error=None)
        console.print(f"[green]✓ {title}[/]: {note}")
    elif status == "dry_run":
        db.update(row["uid"], note=note)
        console.print(f"[cyan]dry-run {title}[/]: {note}")
    else:
        db.update(row["uid"], status=status, error=note)
        console.print(f"[yellow]→ {status}[/] {title}: {note}")
    log_event(cfg, "apply", uid=row["uid"], firm=row["firm"], status=status, mode=ap.mode)
    return status


class Browser:
    """Один постоянный профиль Chromium на запуск: куки и согласия сайтов сохраняются между заявками."""

    def __init__(self, cfg: Config):
        self.cfg, self._pw, self._ctx = cfg, None, None

    def page(self):
        if self._ctx is None:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._ctx = self._pw.chromium.launch_persistent_context(
                str(self.cfg.path(self.cfg.apply.profile_dir)), headless=self.cfg.apply.headless,
                viewport={"width": 1280, "height": 900})
        return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.close()
            self._pw.stop()
            self._ctx = None


def run_once(cfg: Config, db: DB, browser: Browser, mode: str | None = None, poll: bool = True) -> int:
    """Один проход. Вернуть число отправленных заявок."""
    firms = load_firms(cfg)
    if poll:
        watch.run_once(cfg, db, firms)
        materials.write_letters(cfg, db, min_score=cfg.apply.min_score)
    sent, errors = 0, 0
    for row in candidates(cfg, db, firms):
        stop.check()
        if not in_hours(cfg):
            console.print(f"Вне рабочих часов {cfg.apply.active_hours} — заявки подожду.")
            break
        if db.applied_today() >= cfg.apply.daily_limit or sent >= cfg.apply.max_per_run:
            console.print(f"Лимит: сегодня {db.applied_today()} из {cfg.apply.daily_limit}, за запуск {sent}.")
            break
        try:
            plan = prepare(cfg, db, row)
        except SourceError as e:
            console.print(f"[red]Вопросы формы не получены[/] {row['firm']}: {e}")
            continue
        if plan is None:
            continue
        status = apply_one(cfg, db, browser.page(), row, plan, mode)
        if status == "applied":
            sent, errors = sent + 1, 0
            if stop.wait(random.uniform(*cfg.apply.delay_seconds)):
                break
        elif status in ("error", "manual"):
            errors += 1
            if errors >= cfg.apply.max_consecutive_errors:
                console.print("[red]Ошибки подряд — остановка: возможно, поменялась форма.[/]")
                break
    return sent


def loop(cfg: Config, db: DB, mode: str | None = None, once: bool = False) -> None:
    browser = Browser(cfg)
    try:
        while not stop.requested():
            run_once(cfg, db, browser, mode)
            if once:
                break
            console.print(f"Следующий опрос досок через {cfg.watch.every_minutes} мин. Ctrl+C — выход.")
            if stop.wait(cfg.watch.every_minutes * 60):
                break
    except stop.Stopped:
        pass
    finally:
        browser.close()
