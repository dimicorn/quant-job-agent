"""Заполнение формы Ashby (jobs.ashbyhq.com/<name>/<id>/application). Каждое поле лежит в контейнере
[data-field-path=<path>]; вопрос «да/нет» — кнопки button[data-option=yes|no], варианты — радиокнопки или
чекбоксы с подписями, город — автодополнение (role=combobox → role=option). Submit — «Submit Application»."""
from __future__ import annotations

import re

from .answers import Step
from .applier import LABEL_JS, FormApplier, FormError


class AshbyApplier(FormApplier):
    READY = "[data-field-path]"
    SUBMIT = "button:has-text('Submit Application')"

    def root(self, s: Step):
        return self.page.locator(f'[data-field-path="{s.id}"]')

    def exists(self, s: Step) -> bool:
        return self.root(s).count() > 0

    def field(self, s: Step):
        r = self.root(s)
        if s.kind == "file":
            return r.locator("input[type=file]").first
        return r.locator("input:not([type=checkbox]):not([type=radio]):not([type=file]), textarea").first

    def _choices(self, s: Step):
        return self.root(s).locator("input[type=radio], input[type=checkbox]")

    def put_select(self, s: Step) -> None:
        r = self.root(s)
        yesno = r.locator("button[data-option]")
        if yesno.count():
            btn = r.locator(f'button[data-option="{s.value[0].strip().lower()}"]')
            if not btn.count():
                raise FormError(f"«{s.label[:60]}»: нет кнопки «{s.value[0]}»")
            btn.first.scroll_into_view_if_needed()
            btn.first.click()
            return
        boxes = self._choices(s)
        if boxes.count():
            labels = [boxes.nth(i).evaluate(LABEL_JS).strip().lower() for i in range(boxes.count())]
            for v in s.value:
                if v.strip().lower() not in labels:
                    raise FormError(f"в вопросе «{s.label[:60]}» нет варианта «{v}»")
                box = boxes.nth(labels.index(v.strip().lower()))
                box.scroll_into_view_if_needed()
                if not box.is_checked():  # input спрятан под нарисованным кружком: жмём подпись
                    box_id = box.get_attribute("id")
                    if box_id:
                        self.page.locator(f'label[for="{box_id}"]').first.click()
                    else:
                        box.check(force=True)
                self.page.wait_for_timeout(200)
            return
        combo = r.locator("input[role=combobox]")
        if combo.count():
            for v in s.value:
                self._type(combo.first, v[:40])
                opt = self.page.locator("[role=option]").filter(has_text=re.compile(rf"^\s*{re.escape(v)}\s*$", re.I))
                try:
                    opt.first.wait_for(state="visible", timeout=8000)
                except Exception:  # noqa: BLE001
                    raise FormError(f"в списке «{s.label[:60]}» нет варианта «{v}»")
                opt.first.click()
            return
        raise FormError(f"«{s.label[:60]}»: не понял, как устроено поле")

    def shown(self, s: Step) -> str:
        r = self.root(s)
        if s.kind == "location":
            return r.locator("input[role=combobox], input").first.input_value()
        pressed = r.locator('button[data-option][aria-pressed="true"]')
        if r.locator("button[data-option]").count():
            return pressed.first.inner_text() if pressed.count() else ""
        boxes = self._choices(s)
        if boxes.count():
            return ", ".join(boxes.nth(i).evaluate(LABEL_JS) for i in range(boxes.count()) if boxes.nth(i).is_checked())
        combo = r.locator("input[role=combobox]")
        return combo.first.input_value() if combo.count() else r.inner_text()

    def put_location(self, s: Step) -> None:
        """Печатаем город, из подсказок выбираем тот же город в той же стране («Moscow, Russia»)."""
        combo = self.root(s).locator("input[role=combobox]").first
        self._type(combo, s.value[0].split(",")[0].strip())
        opts = self.page.locator("[role=option]")
        try:
            opts.first.wait_for(state="visible", timeout=8000)
        except Exception:  # noqa: BLE001
            raise FormError(f"«{s.label[:60]}»: нет подсказок города")
        hit = self._pick_place(opts, s.value[0])
        if hit is None:
            raise FormError(f"«{s.label[:60]}»: среди подсказок нет «{s.value[0]}»")
        hit.click()
        self.page.wait_for_timeout(300)
