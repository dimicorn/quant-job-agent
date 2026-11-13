"""Заполнение формы Workable (apply.workable.com/<account>/j/<shortcode>/apply/).

У каждого поля контейнер [data-ui=<id>]: у текстовых полей это сам input/textarea (name=<id>), у вопросов с
вариантами — fieldset / div[role=group] с div[role=radio|checkbox] и подписью (aria-labelledby), у списка —
input[role=combobox] с меню #input_<id>_listbox. Телефон — intl-tel-input: номер с «+7» сам выбирает страну.
Address Workable подставляет по IP — печатаем поверх город из profile.city. Submit — [data-ui=apply-button]."""
from __future__ import annotations

import re

from .answers import Step
from .applier import FormApplier, FormError

# подпись варианта: последний id в aria-labelledby (первый — подпись вопроса), иначе текст <label>
OPTION_LABEL_JS = ("e => { const ids = (e.getAttribute('aria-labelledby') || '').split(' ').filter(Boolean);"
                   " const l = ids.length ? document.getElementById(ids[ids.length - 1]) : null;"
                   " return ((l && l.innerText) || (e.closest('label') || {}).innerText || '').trim() }")


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


class WorkableApplier(FormApplier):
    READY = "input[name=firstname]"
    SUBMIT = "[data-ui=apply-button]"

    def root(self, s: Step):
        return self.page.locator(f'[data-ui="{_q(s.id)}"]')

    def exists(self, s: Step) -> bool:
        return self.root(s).count() > 0 or self.page.locator(f'[name="{_q(s.id)}"]').count() > 0

    def field(self, s: Step):
        if s.kind == "file":
            return self.page.locator(f'input[type=file][data-ui="{_q(s.id)}"]').first
        return self.page.locator(f'input[name="{_q(s.id)}"]:not([aria-hidden=true]), '
                                 f'textarea[name="{_q(s.id)}"]').first

    def before_fill(self) -> None:
        """Баннер cookies перекрывает кнопки внизу формы — отказываемся от необязательных cookies."""
        btn = self.page.locator("[data-ui=cookie-consent-decline]")
        try:
            if btn.count() and btn.first.is_visible():
                btn.first.click()
                self.page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001 — баннера нет или уже закрылся
            pass

    def _options(self, s: Step):
        return self.root(s).locator("[role=radio], [role=checkbox]")

    def _labels(self, opts) -> list[str]:
        return [opts.nth(i).evaluate(OPTION_LABEL_JS).lower() for i in range(opts.count())]

    def put_select(self, s: Step) -> None:
        combo = self.root(s).locator("input[role=combobox]")
        if combo.count():
            self._dropdown(s, combo.first)
            return
        opts = self._options(s)
        if not opts.count():
            raise FormError(f"«{s.label[:60]}»: не понял, как устроено поле")
        if s.id == "gdpr" or opts.count() == 1:  # согласие: единственный чекбокс
            if opts.first.get_attribute("aria-checked") != "true":
                opts.first.scroll_into_view_if_needed()
                opts.first.click()
            return
        labels = self._labels(opts)
        for v in s.value:
            want = v.strip().lower()
            if want not in labels:
                raise FormError(f"в вопросе «{s.label[:60]}» нет варианта «{v}»")
            box = opts.nth(labels.index(want))
            box.scroll_into_view_if_needed()
            if box.get_attribute("aria-checked") != "true":
                box.click()
            self.page.wait_for_timeout(200)

    def _dropdown(self, s: Step, combo) -> None:
        for v in s.value:
            combo.scroll_into_view_if_needed()
            combo.click()
            menu = self.page.locator(f'#input_{_q(s.id)}_listbox [role=option]')
            opt = menu.filter(has_text=re.compile(rf"^\s*{re.escape(v)}\s*$", re.I))
            try:
                opt.first.wait_for(state="visible", timeout=8000)
            except Exception:  # noqa: BLE001
                self.page.keyboard.press("Escape")
                raise FormError(f"в списке «{s.label[:60]}» нет варианта «{v}»")
            opt.first.click()
            self.page.wait_for_timeout(200)

    def shown(self, s: Step) -> str:
        if s.kind == "location":
            return self.field(s).input_value()
        combo = self.root(s).locator("input[role=combobox]")
        if combo.count():
            return combo.first.input_value()
        opts = self._options(s)
        if s.id == "gdpr" or opts.count() == 1:
            return "Yes" if opts.count() and opts.first.get_attribute("aria-checked") == "true" else ""
        return ", ".join(opts.nth(i).evaluate(OPTION_LABEL_JS) for i in range(opts.count())
                         if opts.nth(i).get_attribute("aria-checked") == "true")

    def put_location(self, s: Step) -> None:
        """Address — обычное текстовое поле без подсказок: стираем город, подставленный по IP, и печатаем свой."""
        self._type(self.field(s), s.value[0])
