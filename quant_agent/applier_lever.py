"""Заполнение формы Lever (jobs.lever.co/<site>/<id>/apply). Обычный HTML: поле — по атрибуту name,
варианты — radio / checkbox (value = текст варианта) или <select>, город — поле с подсказками .dropdown-location.
После загрузки резюме Lever сам разбирает его и подставляет имя, почту, телефон — агент потом всё равно
печатает свои значения поверх. Submit — кнопка #btn-submit (дальше невидимая hCaptcha)."""
from __future__ import annotations

from .answers import Step
from .applier import LABEL_JS, FormApplier, FormError


def _q(name: str) -> str:
    return name.replace("\\", "\\\\").replace('"', '\\"')


class LeverApplier(FormApplier):
    READY = 'input[name="email"]'
    SUBMIT = "#btn-submit"

    def _all(self, s: Step):
        return self.page.locator(f'[name="{_q(s.id)}"]')

    def field(self, s: Step):
        return self._all(s).first

    def exists(self, s: Step) -> bool:
        return self._all(s).count() > 0

    def _kind(self, s: Step) -> str:
        el = self.field(s)
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        return "select" if tag == "select" else (el.get_attribute("type") or tag)

    def put_select(self, s: Step) -> None:
        kind = self._kind(s)
        if kind == "select":
            self.field(s).select_option(label=s.value[0])
            return
        if kind not in ("radio", "checkbox"):
            raise FormError(f"«{s.label[:60]}»: неизвестный вид поля {kind}")
        for v in s.value:
            box = self.page.locator(f'input[type={kind}][name="{_q(s.id)}"][value="{_q(v)}"]')
            if not box.count():
                raise FormError(f"в вопросе «{s.label[:60]}» нет варианта «{v}»")
            box.first.scroll_into_view_if_needed()
            try:
                box.first.check(timeout=3000)
            except Exception:  # noqa: BLE001 — input спрятан стилями: жмём подпись
                box.first.locator("xpath=ancestor::label[1]").click()
            self.page.wait_for_timeout(200)

    def shown(self, s: Step) -> str:
        if s.kind == "location":
            return self.field(s).input_value()
        kind = self._kind(s)
        if kind == "select":
            return self.field(s).evaluate("e => e.options[e.selectedIndex] ? e.options[e.selectedIndex].text : ''")
        boxes = self._all(s)
        return ", ".join(boxes.nth(i).get_attribute("value") or boxes.nth(i).evaluate(LABEL_JS)
                         for i in range(boxes.count()) if boxes.nth(i).is_checked())

    def put_location(self, s: Step) -> None:
        """Печатаем город, из подсказок выбираем тот же город в той же стране («Moscow, RUS»)."""
        inp = self.field(s)
        self._type(inp, s.value[0].split(",")[0].strip())
        opts = self.page.locator(".dropdown-location")
        try:
            opts.first.wait_for(state="visible", timeout=8000)
        except Exception:  # noqa: BLE001
            raise FormError(f"«{s.label[:60]}»: нет подсказок города")
        hit = self._pick_place(opts, s.value[0])
        if hit is None:
            raise FormError(f"«{s.label[:60]}»: среди подсказок нет «{s.value[0]}»")
        hit.click()
        self.page.wait_for_timeout(300)

    def after_step(self, s: Step) -> None:
        if s.id == "resume":  # Lever разбирает резюме и подставляет поля — ждём, чтобы не перебить ввод
            try:
                self.page.locator(".resume-upload-working").wait_for(state="hidden", timeout=20000)
            except Exception:  # noqa: BLE001 — не дождались: поля всё равно печатаются поверх
                pass
