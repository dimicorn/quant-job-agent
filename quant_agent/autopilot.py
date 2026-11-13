"""Автопилот: опрос досок → оценка → письма → план ответов → заполнение формы Greenhouse → (Submit).

Один проход за запуск (run): опрос и оценка в фоне, заявки сразу из очереди; повтор — по расписанию. Ограничения:
  - формы Greenhouse, Lever, Ashby и Workable (APPLIERS), балл ≥ apply.min_score, есть письмо;
  - не больше одной заявки в фирму за проход и не больше max_applications за cooldown_months;
  - apply.daily_limit в сутки, apply.max_per_run за запуск, рабочие часы apply.active_hours;
  - вопросы анкеты без ответа → needs_answers: отвечаете один раз (quant-agent answers), ответ запоминается.
Режим (apply.mode): assist — Submit жмёте вы; auto — агент; dry_run — без отправки."""
from __future__ import annotations

import random
import re
import threading
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from . import answers, materials, stop, watch
from .applier import BrowserClosed, FormError, GreenhouseApplier
from .applier_ashby import AshbyApplier
from .applier_lever import LeverApplier
from .applier_workable import WorkableApplier
from .config import Config
from .db import DB, now
from .events import log_event
from .firms import Firm, load_firms
from .log import console
from .sources import SourceError, ashby, greenhouse, lever, workable

# площадка → (модуль с questions() и form_url(), класс заполнения формы)
APPLIERS = {"greenhouse": (greenhouse, GreenhouseApplier), "lever": (lever, LeverApplier),
            "ashby": (ashby, AshbyApplier), "workable": (workable, WorkableApplier)}


def in_hours(cfg: Config, t: datetime | None = None) -> bool:
    t = t or datetime.now()
    a, b = cfg.apply.active_hours
    return a <= t.hour < b and not (cfg.apply.weekdays_only and t.weekday() >= 5)


def candidates(cfg: Config, db: DB, firms: list[Firm], skip: set[str] | None = None,
               later: set[str] | None = None) -> list[dict]:
    """Лучшая подходящая вакансия каждой фирмы, где лимит заявок ещё не исчерпан. По tier, потом по баллу.
    skip — уже пробованные в этом проходе; later — фирмы, куда в этом проходе уже подали (идут в конце)."""
    by_name = {f.name: f for f in firms}
    rows = db.for_review(cfg.apply.min_score, {f.name: f.tier for f in firms})
    out, seen = [], set()
    for r in rows:
        f = by_name.get(r["firm"])
        if (r["ats"] not in APPLIERS or not r.get("letter") or r["firm"] in seen or f is None
                or r["uid"] in (skip or ())):
            continue
        if db.applications(f.name, f.cooldown_months) >= f.max_applications:
            continue
        seen.add(r["firm"])
        out.append(r)
    return sorted(out, key=lambda r: r["firm"] in (later or ()))  # sorted устойчив: внутри групп порядок тот же


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


# Страница формы есть, а формы нет: фирма удалила вакансию или ушла с этой ATS (Pinely: Ashby → Workable,
# а API Ashby ещё отдаёт старые вакансии)
GONE = re.compile(r"page not found|page you requested was not found|no longer (?:available|accepting)|"
                  r"job (?:has been|was) (?:closed|filled|removed)|position (?:has been|is) (?:closed|filled)|\b404\b", re.I)


def _not_opened(db: DB, page, row: dict, title: str, e: Exception) -> str:
    """Форма не открылась. Удалена — статус gone (опрос доски его не вернёт); иначе сеть — вакансия остаётся
    в очереди до следующего прохода."""
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except PlaywrightError:
        text = ""
    if GONE.search(text):
        db.update(row["uid"], status="gone", error=f"формы нет на сайте: {page.url}", decided_at=now())
        console.print(f"[yellow]→ gone[/] {title}: формы нет на сайте ({page.url}) — фирма убрала вакансию или сменила ATS")
        return "gone"
    console.print(f"[yellow]Форма не открылась[/] {title}: {str(e).splitlines()[0][:150]} — попробую в следующий проход")
    return "retry"


def apply_one(cfg: Config, db: DB, page, row: dict, plan: answers.Plan, mode: str | None = None) -> str:
    title = f"{row['firm']} — {row['title']}"
    console.print(f"Заполняю: {title} ({row['score']})")
    source, cls = APPLIERS[row["ats"]]
    ap = cls(page, cfg, mode)
    ap.firm = row["firm"]
    files = Path(cfg.path("materials/tmp"))
    files.mkdir(parents=True, exist_ok=True)
    try:
        try:
            ap.open(source.form_url(row["token"], row["ext_id"]))
        except PlaywrightError as e:
            if page.is_closed():
                raise
            return _not_opened(db, page, row, title, e)
        ap.fill(plan, files)
        problems = ap.verify(plan)
        if problems:
            raise FormError("; ".join(problems))
        status, note = ap.submit(title)
    except FormError as e:
        status, note = "manual", f"форма: {e}; снимок: {ap.screenshot(title)}"
    except (BrowserClosed, PlaywrightError) as e:
        if not page.is_closed() and not isinstance(e, BrowserClosed):
            status, note = "error", f"браузер: {str(e).splitlines()[0][:200]}"
        else:
            console.print(f"[yellow]Окно браузера закрыто — {title} остаётся в очереди.[/]")
            return "closed"
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
        try:
            return self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        except PlaywrightError:  # окно закрыли — запускаем браузер заново
            self.close()
            return self.page()

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except PlaywrightError:  # уже закрыт
                pass
            self._pw.stop()
            self._ctx = None


def _produce(cfg: Config, firms: list[Firm], producing: threading.Event) -> None:
    """Опрос досок, оценка и письма — в фоне, пока заявки подаются из очереди. Своё соединение с базой."""
    try:
        db = DB(cfg.path(cfg.db_path))
        watch.run_once(cfg, db, firms)
        materials.write_letters(cfg, db, min_score=cfg.apply.min_score)
    except stop.Stopped:
        pass
    except Exception as e:  # noqa: BLE001 — заявки из уже готовой очереди продолжаются
        console.print(f"[red]Опрос и оценка: {e}[/]")
    finally:
        producing.clear()


def run_once(cfg: Config, db: DB, browser: Browser, mode: str | None = None, poll: bool = True) -> int:
    """Один проход, как autopilot в hh-agent: опрос досок, оценка и письма идут в фоне, заявки подаются сразу из
    очереди; после каждой заявки очередь пересчитывается (лимиты на фирму). Проход заканчивается, когда очередь
    пуста и фон закончил, или по лимиту и рабочим часам. Вернуть число отправленных заявок."""
    firms = load_firms(cfg)
    producing = threading.Event()
    bg = None
    if poll:
        producing.set()
        bg = threading.Thread(target=_produce, args=(cfg, firms, producing), name="опрос-оценка", daemon=True)
        bg.start()
    sent, errors, tried, done_firms = 0, 0, set(), set()
    try:
        while not stop.requested():
            if not in_hours(cfg):
                console.print(f"Вне рабочих часов {cfg.apply.active_hours} — заявки подожду.")
                break
            if db.applied_today() >= cfg.apply.daily_limit or sent >= cfg.apply.max_per_run:
                console.print(f"Лимит: сегодня {db.applied_today()} из {cfg.apply.daily_limit}, за запуск {sent}.")
                break
            busy = producing.is_set()  # до запроса очереди: фон мог закончить, пока мы её считали
            row = next(iter(candidates(cfg, db, firms, tried, done_firms)), None)
            if row is None:
                if busy:  # опрос и оценка ещё шли — подходящие вакансии могли появиться
                    if stop.wait(15):
                        break
                    continue
                break
            tried.add(row["uid"])
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
                done_firms.add(row["firm"])
                if stop.wait(random.uniform(*cfg.apply.delay_seconds)):
                    break
            elif status == "closed":
                browser.close()  # следующая заявка откроет новое окно
                if stop.wait(30):
                    break
            elif status in ("error", "manual", "retry"):
                errors += 1
                if errors >= cfg.apply.max_consecutive_errors:
                    console.print("[red]Ошибки подряд — остановка: возможно, поменялась форма.[/]")
                    break
    finally:
        if bg is not None and bg.is_alive() and not stop.requested():
            console.print("Заявки на этот проход закончены — дожидаюсь оценки и писем…")
            while bg.is_alive() and not stop.wait(1):
                pass
    return sent


def run(cfg: Config, db: DB, mode: str | None = None) -> int:
    """Один проход и выход, как autopilot в hh-agent; повторять — запуском по расписанию. Вернуть число заявок."""
    browser = Browser(cfg)
    sent = 0
    try:
        sent = run_once(cfg, db, browser, mode)
        console.print(f"Проход закончен: отправлено {sent}, сегодня {db.applied_today()} из {cfg.apply.daily_limit}.")
    except stop.Stopped:
        pass
    finally:
        browser.close()
    return sent
