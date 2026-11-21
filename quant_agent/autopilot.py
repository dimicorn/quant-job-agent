"""Автопилот: опрос досок → оценка → письма → план ответов → заполнение формы Greenhouse → (Submit).

Один проход за запуск (run): опрос и оценка в фоне, заявки сразу из очереди; повтор — по расписанию. Ограничения:
  - формы Greenhouse, Lever, Ashby и Workable (APPLIERS), балл ≥ apply.min_score, есть письмо;
  - не больше max_applications в фирму за cooldown_months; вторая заявка в фирму за проход — после первых
    заявок в другие фирмы;
  - apply.daily_limit в сутки, apply.max_per_run за запуск, рабочие часы apply.active_hours;
  - вопросы анкеты без ответа → needs_answers: отвечаете один раз (quant-agent answers), ответ запоминается.
Режим (apply.mode): assist — Submit жмёте вы; auto — агент; dry_run — без отправки."""
from __future__ import annotations

import random
import re
import threading
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from rich.markup import escape

from . import answers, materials, stop, watch
from .applier import BrowserClosed, FormApplier, FormError, GreenhouseApplier
from .applier_ashby import AshbyApplier
from .applier_lever import LeverApplier
from .applier_workable import WorkableApplier
from .config import Config
from .db import DB, now
from .events import log_event
from .firms import Firm, load_firms
from .llm import LimitReached
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
        console.print(f"[yellow]? {escape(row['firm'])} — {escape(row['title'])}[/]: вопросов без ответа — {len(plan.missing)} "
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
        console.print(f"[yellow]→ gone[/] {escape(title)}: формы нет на сайте ({page.url}) — фирма убрала вакансию или сменила ATS")
        return "gone"
    console.print(f"[yellow]Форма не открылась[/] {escape(title)}: {escape(str(e).splitlines()[0][:150])} — попробую в следующий проход")
    return "retry"


def _shown(cfg: Config, note: str) -> str:
    """Заметка для журнала: снимок — отдельной строкой и путём от папки проекта (в базе — полный путь)."""
    note = escape(note.replace(f"{cfg.base_dir}/", ""))
    return note.replace("; снимок: ", "\n    [dim]снимок:[/] ")


def apply_one(cfg: Config, db: DB, page, row: dict, plan: answers.Plan, mode: str | None = None) -> str:
    title = f"{row['firm']} — {row['title']}"
    console.print(f"Заполняю: {escape(title)} [bold]({row['score']})[/]")
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
            console.print(f"[yellow]Окно браузера закрыто — {escape(title)} остаётся в очереди.[/]")
            return "closed"
    if status == "applied":
        db.update(row["uid"], status="applied", applied_at=now(), note=note, error=None)
        console.print(f"[green]✓ заявка отправлена[/] [dim]({escape(note)})[/]")
    elif status == "dry_run":
        db.update(row["uid"], note=note)
        console.print(f"[cyan]dry-run[/]: {_shown(cfg, note)}")
    else:
        db.update(row["uid"], status=status, error=note)
        console.print(f"[yellow]→ {status}[/]: {_shown(cfg, note)}")
    log_event(cfg, "apply", uid=row["uid"], firm=row["firm"], status=status, mode=ap.mode)
    return status


class Browser:
    """Один постоянный профиль браузера на запуск: куки и согласия сайтов сохраняются между заявками.

    Форму, которую агент не смог заполнить (manual), он оставляет вам во вкладке и больше её не трогает:
    следующая заявка открывается в новой вкладке, а в конце прохода агент ждёт, пока вы отправите
    или закроете оставленные формы."""

    # Капчи (Cloudflare Turnstile, hCaptcha) не пропускают браузер, который выдаёт автоматизацию:
    # флаг --enable-automation и navigator.webdriver. Убираем оба; полной маскировки это не даёт.
    ARGS = ["--disable-blink-features=AutomationControlled"]
    IGNORE = ["--enable-automation"]

    def __init__(self, cfg: Config):
        self.cfg, self._pw, self._ctx, self._page = cfg, None, None, None
        self.handed: list[tuple] = []  # (вкладка, строка вакансии) — формы, оставленные вам и ещё открытые
        self._given: list = []         # все вкладки, отданные вам: агент их больше не берёт

    def _launch(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        a = self.cfg.apply
        kw = dict(headless=a.headless, viewport={"width": 1280, "height": 900},
                  args=self.ARGS, ignore_default_args=self.IGNORE)
        profile = str(self.cfg.path(a.profile_dir))
        if a.browser_channel:
            try:
                return self._pw.chromium.launch_persistent_context(profile, channel=a.browser_channel, **kw)
            except PlaywrightError as e:
                console.print(f"[yellow]Браузер {a.browser_channel} не запустился ({str(e).splitlines()[0][:120]}) — "
                              f"беру Chromium из Playwright.[/]")
        return self._pw.chromium.launch_persistent_context(profile, **kw)

    def page(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._ctx is None:
            self._ctx = self._launch()
        try:
            free = [p for p in self._ctx.pages if p not in self._given]
            self._page = free[0] if free else self._ctx.new_page()
        except PlaywrightError:  # окно закрыли — запускаем браузер заново
            self.close()
            return self.page()
        return self._page

    def hand_off(self, row: dict) -> None:
        """Форма осталась вам: вкладку больше не трогаем, следующая заявка — в новой вкладке."""
        page, self._page = self._page, None
        if page is None or page.is_closed():
            return
        self.handed.append((page, row))
        self._given.append(page)
        console.print("    [dim]форма оставлена вам во вкладке: отправьте её сами или закройте[/]")
        try:
            page.bring_to_front()
        except PlaywrightError:
            pass

    def check_handed(self, db: DB) -> int:
        """Формы, оставленные вам: отправили — заявка applied, закрыли вкладку — забываем. Вернуть, сколько
        ещё открыто."""
        left = []
        for page, row in self.handed:
            if page.is_closed():
                continue
            if FormApplier(page, self.cfg).confirmed():
                db.update(row["uid"], status="applied", applied_at=now(), note="отправлено вами (manual)", error=None)
                log_event(self.cfg, "apply", uid=row["uid"], firm=row["firm"], status="applied", mode="manual")
                console.print(f"[green]✓ {escape(row['firm'])} — {escape(row['title'])}[/]: отправлено вами")
                continue
            left.append((page, row))
        self.handed = left
        return len(left)

    def wait_handed(self, db: DB) -> None:
        """Перед закрытием браузера: дождаться, пока вы отправите или закроете оставленные формы."""
        if self._ctx is None or not self.check_handed(db):
            return
        console.print(f"[bold]Оставленных вам форм: {len(self.handed)}[/] — отправьте их или закройте вкладки, "
                      f"тогда браузер закроется. Ctrl+C — закрыть сразу.")
        watch.notify("Формы ждут вас", ", ".join(f"{r['firm']} — {r['title']}" for _, r in self.handed))
        while self.check_handed(db) and not stop.wait(3):
            pass

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close()
            except PlaywrightError:  # уже закрыт
                pass
            self._pw.stop()
            self._ctx = None
        self._page, self.handed, self._given = None, [], []


def _produce(cfg: Config, firms: list[Firm], producing: threading.Event) -> None:
    """Опрос досок, оценка и письма — в фоне, пока заявки подаются из очереди. Своё соединение с базой."""
    try:
        db = DB(cfg.path(cfg.db_path))
        watch.run_once(cfg, db, firms)
        materials.write_letters(cfg, db, min_score=cfg.apply.min_score)
    except stop.Stopped:
        pass
    except (Exception, SystemExit) as e:  # noqa: BLE001 — заявки из уже готовой очереди продолжаются;
        # SystemExit (нет входа в Codex, неверный ключ API) в потоке Python гасит без следа — печатаем сами
        console.print(f"[red]Опрос и оценка: {escape(str(e))}[/]")
    finally:
        producing.clear()


def run_once(cfg: Config, db: DB, browser: Browser, mode: str | None = None, poll: bool = True,
             outcomes: Counter | None = None) -> int:
    """Один проход, как autopilot в hh-agent: опрос досок, оценка и письма идут в фоне, заявки подаются сразу из
    очереди; после каждой заявки очередь пересчитывается (лимиты на фирму). Проход заканчивается, когда очередь
    пуста и фон закончил, или по лимиту и рабочим часам. Вернуть число отправленных заявок; итоги остальных
    заявок (manual, needs_answers…) — в outcomes."""
    firms = load_firms(cfg)
    outcomes = Counter() if outcomes is None else outcomes
    a = cfg.apply
    console.print(f"{'[bold]DRY-RUN[/] — ' if (mode or a.mode) == 'dry_run' else ''}режим {mode or a.mode}: "
                  f"в очереди {len(candidates(cfg, db, firms))}, за проход максимум {a.max_per_run}, "
                  f"сегодня {db.applied_today()} из {a.daily_limit}" + (" · опрос досок в фоне" if poll else ""))
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
            browser.check_handed(db)  # формы, оставленные вам раньше, могли уже уйти
            try:
                plan = prepare(cfg, db, row)
            except SourceError as e:
                console.print(f"[red]Вопросы формы не получены[/] {escape(row['firm'])}: {escape(str(e))}")
                continue
            except LimitReached as e:  # без LLM анкеты не заполнить — до следующего прохода
                console.print(f"[red]Лимиты LLM кончились[/] — заявки до следующего прохода: {escape(str(e))}")
                break
            except RuntimeError as e:  # модель ответила не по схеме или не ответила — вакансия остаётся в очереди
                console.print(f"[red]Ответы для анкеты не готовы[/] {escape(row['firm'])} — "
                              f"{escape(row['title'])}: {escape(str(e)[:200])}")
                outcomes["error"] += 1
                continue
            if plan is None:
                outcomes["needs_answers"] += 1
                continue
            status = apply_one(cfg, db, browser.page(), row, plan, mode)
            outcomes[status] += 1
            if status == "manual":
                browser.hand_off(row)
            if status == "applied":
                sent, errors = sent + 1, 0
                done_firms.add(row["firm"])
                if db.applied_today() >= a.daily_limit or sent >= a.max_per_run:
                    continue  # лимит — пауза не нужна, проход закончится на следующем круге
                delay = random.uniform(*a.delay_seconds)
                console.print(f"[dim]Пауза до следующей заявки: {delay / 60:.1f} мин "
                              f"(до {datetime.now() + timedelta(seconds=delay):%H:%M:%S})[/]")
                if stop.wait(delay):
                    break
            elif status == "closed":
                if not browser.check_handed(db):  # оставленные вам формы не закрываем
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
    sent, outcomes = 0, Counter()
    with console.tagged("заявки"):
        try:
            sent = run_once(cfg, db, browser, mode, outcomes=outcomes)
            console.print(summary(cfg, db, sent, outcomes))
        except stop.Stopped:
            pass
        finally:
            if not stop.requested():
                browser.wait_handed(db)
            browser.close()
    return sent


OUTCOMES = {"dry_run": "dry-run", "manual": "оставлено вам", "needs_answers": "ждут ответов (quant-agent answers)",
            "error": "ошибок", "retry": "не открылось", "gone": "удалено с сайта", "closed": "окно закрыто"}


def summary(cfg: Config, db: DB, sent: int, outcomes: Counter) -> str:
    """Итог прохода, как в hh-agent: что отправлено, что ждёт вас, сколько всего сегодня."""
    parts = [f"отправлено {sent}"] + [f"{label} {outcomes[k]}" for k, label in OUTCOMES.items() if outcomes[k]]
    return (f"[bold]Готово:[/] {', '.join(parts)}. "
            f"Сегодня всего заявок: {db.applied_today()}/{cfg.apply.daily_limit}")
