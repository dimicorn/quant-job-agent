"""Заполнение формы заявки по плану из answers.plan(): общая часть (FormApplier) и Greenhouse
(job-boards.greenhouse.io/embed/job_app). Lever и Ashby — в applier_lever.py и applier_ashby.py.

Поведение как у hh-agent: «читаем» вакансию, пауза между полями, печать посимвольно. Режимы (apply.mode):
  assist  — агент заполняет и проверяет форму, Submit нажимаете вы; агент ждёт страницу подтверждения;
  auto    — агент сам нажимает Submit и ждёт подтверждения (reCAPTCHA с задачей — уведомление и ожидание вас);
  dry_run — заполнить, сделать снимок экрана, ничего не отправлять.
Каждое поле после заполнения проверяется по тому, что видно в форме; не сошлось — заявку не отправляем."""
from __future__ import annotations

import random
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from . import mail, stop
from .answers import Plan, Step
from .config import Config
from .log import console
from .watch import notify

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
OPT = ".select__option"
DOM_IDS = {"location": "candidate-location"}  # id поля в API → id элемента в форме, где они не совпадают
CONFIRM = re.compile(r"thank you for applying|application (has been |was )?(successfully )?(submitted|received)"
                     r"|successfully submitted|we('| ha)ve received your application", re.I)
# Поле кода из письма после Submit: у Greenhouse — 8 клеток security-input-0…7
CODE_INPUTS = ("input[id^='security-input'], input[id*='security-code'], input[id*='security_code'], "
               "input[autocomplete='one-time-code']")
# Подпись чекбокса или радиокнопки: обёртка <label> или <label for=id>
LABEL_JS = ("e => ((e.closest('label') || document.querySelector(`label[for=\"${CSS.escape(e.id)}\"]`) || {})"
            ".innerText || '').trim()")


def place_match(option: str, wanted: str) -> bool:
    """Подсказка города подходит: точное совпадение или тот же город и та же страна в другой записи
    («Moscow, RUS», «Moscow, Russia» для «Moscow, Moscow City, Russia»)."""
    o = [x.strip().lower() for x in option.split(",") if x.strip()]
    w = [x.strip().lower() for x in wanted.split(",") if x.strip()]
    if not o or not w:
        return False
    if o == w:
        return True
    return len(o) > 1 and len(w) > 1 and o[0] == w[0] and (w[-1].startswith(o[-1]) or o[-1].startswith(w[-1]))


def fit(text: str, limit: int) -> str | None:
    """Ответ в лимит поля (maxlength) целыми предложениями; первое не влезает — None."""
    out = ""
    for sent in re.split(r"(?<=[.!?])\s+", text.strip()):
        nxt = f"{out} {sent}".strip()
        if len(nxt) > limit:
            break
        out = nxt
    return out or None


def _norm(text: str) -> str:
    return " ".join(text.split())


class FormError(Exception):
    """Форма не заполнилась как надо — заявку не отправляем."""


class BrowserClosed(Exception):
    """Окно браузера закрыли — вакансия остаётся в очереди."""


class FormApplier:
    """Общее для всех площадок: паузы, печать, проверка, снимок, отправка по режиму.
    Наследник задаёт READY (что ждать при открытии), SUBMIT (кнопка отправки) и как найти поле,
    выбрать вариант и прочитать выбранное (field, put_select, put_location, shown)."""
    READY = "#first_name"
    SUBMIT = "button[type=submit]"

    def __init__(self, page, cfg: Config, mode: str | None = None):
        self.page, self.cfg = page, cfg
        self.mode = mode or cfg.apply.mode
        self.firm = ""            # для поиска письма с кодом: письма, где упомянута фирма, — первыми
        self._code_since: datetime | None = None
        self._code_done = False

    # ── паузы и ввод ──
    def _beat(self) -> None:
        if stop.wait(random.uniform(*self.cfg.apply.action_seconds)):
            raise stop.Stopped

    def _type(self, loc, text: str) -> None:
        lo, hi = self.cfg.apply.typing_delay_ms
        loc.scroll_into_view_if_needed()
        loc.click()
        loc.fill("")
        if hi <= 0:
            loc.fill(text)
        else:
            for ch in text:
                loc.press_sequentially(ch, delay=0)
                self.page.wait_for_timeout(random.uniform(lo, hi))

    def _letter_pdf(self, text: str, files_dir: Path) -> str:
        """Письмо в поле-файле — отдельный PDF."""
        from .materials import render_pdf, to_html
        html = to_html(escape(text).replace("\n", "\n\n"))  # «<», «&» в письме — текст, не разметка
        # второй sync Playwright нельзя запустить в потоке, где уже работает браузер формы
        with ThreadPoolExecutor(1) as ex:
            return str(ex.submit(render_pdf, html, files_dir / "cover_letter.pdf").result())

    def _pick_place(self, options, wanted: str):
        """Из видимых подсказок города — подходящая (place_match) или None."""
        for i in range(options.count()):
            o = options.nth(i)
            if place_match(o.inner_text(), wanted):
                return o
        return None

    # ── что задаёт площадка ──
    def field(self, s: Step):
        """Элемент для ввода текста и файла."""
        raise NotImplementedError

    def exists(self, s: Step) -> bool:
        return self.field(s).count() > 0

    def put_select(self, s: Step) -> None:
        raise NotImplementedError

    def put_location(self, s: Step) -> None:
        raise NotImplementedError

    def shown(self, s: Step) -> str:
        """Что выбрано в поле с вариантами или в поле города — для проверки."""
        raise NotImplementedError

    def before_fill(self) -> None:
        pass

    def after_step(self, s: Step) -> None:
        pass

    # ── форма ──
    def open(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.locator(self.READY).first.wait_for(timeout=30000)
        # «читаем» вакансию: страница прокручивается, как у человека
        end = datetime.now().timestamp() + random.uniform(*self.cfg.apply.read_seconds)
        while datetime.now().timestamp() < end and not stop.requested():
            self.page.mouse.wheel(0, random.randint(200, 600))
            stop.wait(random.uniform(0.8, 2.0))
        self.page.evaluate("window.scrollTo(0, 0)")

    def _step(self, s: Step, files_dir: Path) -> None:
        if s.kind in ("text", "textarea"):
            el = self.field(s)
            value = s.value[0] if s.kind == "textarea" else _norm(s.value[0])  # в однострочном поле нет переносов
            limit = int(el.get_attribute("maxlength") or 0)
            if limit > 0 and len(value) > limit:
                short = fit(value, limit)
                if not short:
                    raise FormError(f"«{s.label[:60]}»: ответ длиннее лимита поля ({len(value)} > {limit})")
                console.print(f"[yellow]«{s.label[:60]}»: ответ сокращён до {len(short)} символов (лимит {limit})[/]")
                value = short
            s.value = [value]  # verify сверяет с тем, что напечатано
            self._type(el, value)
        elif s.kind == "file":
            path = self._letter_pdf(s.value[0], files_dir) if s.source == "letter" else s.value[0]
            self.field(s).set_input_files(path)
        elif s.kind in ("select", "multiselect"):
            self.put_select(s)
        elif s.kind == "location":
            self.put_location(s)
        self._beat()

    def fill(self, plan: Plan, files_dir: Path) -> None:
        self.before_fill()
        for s in plan.steps:
            if not self.exists(s):
                raise FormError(f"в форме нет поля {s.id} («{s.label}»)")
            self._step(s, files_dir)
            self.after_step(s)

    def verify(self, plan: Plan) -> list[str]:
        """Что в форме не так: значения не совпали с планом."""
        problems = []
        for s in plan.steps:
            if s.kind == "location":
                shown = self.shown(s)
                if not place_match(shown, s.value[0]):
                    problems.append(f"«{s.label[:60]}»: город не выбран ({shown!r})")
            elif s.kind in ("select", "multiselect"):
                shown = self.shown(s).lower()
                for v in s.value:
                    if v.lower() not in shown:
                        problems.append(f"«{s.label[:60]}»: не выбрано «{v}»")
            elif s.kind in ("text", "textarea"):
                got = self.field(s).input_value()
                if s.id == "phone" or "phone" in s.label.lower():  # виджеты переформатируют номер: сверяем цифры
                    g, w = re.sub(r"\D", "", got), re.sub(r"\D", "", s.value[0])
                    if len(g) < 7 or not (w.endswith(g) or g.endswith(w)):
                        problems.append(f"«{s.label[:60]}»: номер не совпал ({got})")
                elif _norm(got) != _norm(s.value[0]):
                    problems.append(f"«{s.label[:60]}»: текст не совпал ({len(got)} из {len(s.value[0])} символов)")
        return problems

    def screenshot(self, name: str) -> Path | str:
        safe = re.sub(r"[^\w-]+", "-", name)[:60]
        out = self.cfg.path(f"debug/{datetime.now():%Y%m%d-%H%M%S}-{safe}.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(out), full_page=True)
        except Exception:  # noqa: BLE001 — окно закрыли: снимка нет, но заявка не должна ронять проход
            return "нет (окно закрыто)"
        return out

    def confirmed(self) -> bool:
        try:
            url = self.page.url
            return "confirmation" in url or url.rstrip("/").endswith("/thanks") or bool(CONFIRM.search(self.page.locator("body").inner_text(timeout=2000)))
        except Exception:  # noqa: BLE001 — страница перезагружается
            return False

    def _code_boxes(self):
        boxes = self.page.locator(CODE_INPUTS)
        try:
            return boxes if boxes.count() and boxes.first.is_visible() else None
        except Exception:  # noqa: BLE001 — страница перезагружается
            return None

    def _enter_code(self, boxes) -> None:
        """Форма просит код из письма: берём его из почты, вписываем и снова жмём Submit (решение об отправке
        уже принято — вами в assist или режимом auto). Почты нет или письмо не пришло — просим ввести вручную."""
        self._code_done = True
        if not mail.configured(self.cfg):
            notify("Нужен код из письма", "введите security code из почты и нажмите Submit")
            console.print("[yellow]Форма просит код из письма — введите его в окне браузера "
                          "(чтобы агент делал это сам, настройте почту: секция mail в config.yaml).[/]")
            return
        console.print("Форма просит код из письма — жду его в почте…")
        try:
            code = mail.wait_code(self.cfg, self._code_since or datetime.now(UTC), self.firm)
        except mail.MailError as e:
            code = None
            console.print(f"[yellow]Почта: {e}[/]")
        if not code:
            notify("Нужен код из письма", "агент не нашёл письмо с кодом — введите его и нажмите Submit")
            console.print("[yellow]Письма с кодом нет — введите код в окне браузера сами.[/]")
            return
        n = boxes.count()
        if n > 1:  # по клетке на символ
            for i, ch in enumerate(code[:n]):
                boxes.nth(i).fill(ch)
        else:
            boxes.first.fill(code)
        self._beat()
        self.page.locator(self.SUBMIT).first.click()
        console.print("Код из письма введён, форма отправлена.")

    def _wait_confirmed(self, seconds: float) -> bool:
        end = datetime.now().timestamp() + seconds
        while datetime.now().timestamp() < end:
            if self.page.is_closed():
                raise BrowserClosed
            if self.confirmed():
                return True
            if not self._code_done:
                boxes = self._code_boxes()
                if boxes is not None:
                    self._enter_code(boxes)
                    continue
            if stop.wait(2):
                return False
        return False

    def submit(self, title: str) -> tuple[str, str]:
        """После fill и verify. Вернуть (статус, заметка)."""
        if self.mode == "dry_run":
            return "dry_run", f"снимок: {self.screenshot(title)}"
        self._code_since = datetime.now(UTC)  # письмо с кодом придёт после Submit
        if self.mode == "assist":
            self.page.bring_to_front()
            notify("Форма заполнена", f"{title}: проверьте и нажмите Submit application")
            console.print(f"[bold]Форма заполнена.[/] Проверьте её в окне браузера и нажмите «Submit application». "
                          f"Жду подтверждения до {self.cfg.apply.submit_wait_minutes} мин…")
            if self._wait_confirmed(self.cfg.apply.submit_wait_minutes * 60):
                return "applied", "отправлено вами (assist)"
            return "manual", "не дождался отправки — форма осталась открытой"
        self.page.locator(self.SUBMIT).first.click()
        if self._wait_confirmed(45):
            return "applied", "отправлено автоматически"
        notify("Нужна помощь", f"{title}: после Submit нет подтверждения — возможно, reCAPTCHA")
        console.print("[yellow]Нет подтверждения после Submit — возможно, reCAPTCHA. Пройдите её в окне браузера.[/]")
        if self._wait_confirmed(self.cfg.apply.submit_wait_minutes * 60):
            return "applied", "отправлено после капчи"
        return "error", f"нет подтверждения после Submit; снимок: {self.screenshot(title)}"


class GreenhouseApplier(FormApplier):
    """job-boards.greenhouse.io: поля по id, выпадающие списки — react-select, блок образования и страна —
    стандартные блоки формы, заполняются из config.profile."""
    READY = "#first_name"
    SUBMIT = "button[type=submit]"

    def _css(self, field_id: str) -> str:
        field_id = DOM_IDS.get(field_id, field_id)
        return "#" + re.sub(r"([\[\]])", r"\\\1", field_id)

    def field(self, s: Step):
        return self.page.locator(self._css(s.id))

    def put_select(self, s: Step) -> None:
        if self._boxes(s.id).count():
            self._check(s.id, s.value)
        else:
            self._select(s.id, s.value)

    def put_location(self, s: Step) -> None:  # подсказки геокодера: ищем по городу, выбираем точный вариант
        self._select(s.id, s.value, search=s.value[0].split(",")[0])

    def shown(self, s: Step) -> str:
        return self._selected(s.id)

    def before_fill(self) -> None:
        if self.page.locator("#country").count() and self.cfg.profile.country:
            self._select("country", [self.cfg.profile.country], search=True)
            self._beat()

    def after_step(self, s: Step) -> None:
        if s.id == "resume":  # блок образования идёт сразу после резюме
            self._education()

    def _close_menu(self) -> None:
        self.page.keyboard.press("Escape")
        self.page.keyboard.press("Tab")

    def _select(self, field_id: str, values: list[str], search: bool | str = False) -> None:
        """React-select: клик → (поиск) → вариант с точно таким текстом. search — строка поиска (True — сам вариант)."""
        inp = self.page.locator(self._css(field_id))
        for v in values:
            inp.scroll_into_view_if_needed()
            inp.click()
            if search:
                query = search if isinstance(search, str) else v[:40]
                inp.fill("")  # после неудачной попытки в поле остаётся прежний поиск
                inp.press_sequentially(query, delay=random.randint(30, 80))
            # точный вариант без учёта регистра: справочники вузов у фирм разные («… Of Physics And Technology»);
            # у списка стран рядом с названием — телефонный код («Russia +7»)
            opt = self.page.locator(OPT).filter(
                has_text=re.compile(rf"^\s*{re.escape(v)}(\s*\+\d[\d-]*)?\s*$", re.I))
            try:
                opt.first.wait_for(state="visible", timeout=8000)
            except Exception:  # noqa: BLE001 — варианта нет: дальше разберётся проверка
                self._close_menu()
                raise FormError(f"в списке «{field_id}» нет варианта «{v}»")
            opt.first.click()
            self.page.wait_for_timeout(200)
        self._close_menu()

    # Вопрос с несколькими ответами Greenhouse иногда показывает группой чекбоксов, а не списком:
    # <input type=checkbox name="question_N[]" id="question_N[]_<вариант>"> с подписью <label>.
    _LABEL = LABEL_JS

    def _boxes(self, field_id: str):
        return self.page.locator(f'input[type=checkbox][name="{field_id}"]')

    def _check(self, field_id: str, values: list[str]) -> None:
        boxes = self._boxes(field_id)
        labels = [boxes.nth(i).evaluate(self._LABEL).lower() for i in range(boxes.count())]
        for v in values:
            if v.strip().lower() not in labels:
                raise FormError(f"в группе «{field_id}» нет варианта «{v}»")
            box = boxes.nth(labels.index(v.strip().lower()))
            box.scroll_into_view_if_needed()
            if not box.is_checked():
                box.check()
            self.page.wait_for_timeout(random.uniform(150, 450))

    def _checked(self, field_id: str) -> str:
        boxes = self._boxes(field_id)
        return ", ".join(boxes.nth(i).evaluate(self._LABEL) for i in range(boxes.count()) if boxes.nth(i).is_checked())

    def _selected(self, field_id: str) -> str:
        if self._boxes(field_id).count():
            return self._checked(field_id)
        return self.page.locator(self._css(field_id)).evaluate(
            "e => (e.closest('.select__control') || e.parentElement).innerText")

    def _pick(self, field_id: str, names: list[str], search: bool = False) -> None:
        """Первое из названий, которое есть в списке: справочники вузов и степеней у фирм разные."""
        for i, name in enumerate(names):
            try:
                self._select(field_id, [name], search=search)
                return
            except FormError:
                if i == len(names) - 1:
                    raise
                self.page.wait_for_timeout(400)  # меню прошлой попытки ещё закрывается

    def _education(self) -> None:
        edu = self.cfg.profile.education
        if not edu or not self.page.locator("#school--0").count():
            return
        e = edu[0]
        self._pick("school--0", [e.school, *e.school_aliases], search=True)
        self._beat()
        self._pick("degree--0", [e.degree, *e.degree_aliases])
        self._beat()
        self._pick("discipline--0", [e.discipline, *e.discipline_aliases], search=True)
        self._beat()
        for prefix, (month, year) in (("start", e.start), ("end", e.end)):
            if self.page.locator(f"#{prefix}-month--0").count():
                self._select(f"{prefix}-month--0", [MONTHS[month - 1]])
                self._type(self.page.locator(f"#{prefix}-year--0"), str(year))
                self._beat()


Applier = GreenhouseApplier  # старое имя: тесты и cli
