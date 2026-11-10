"""Поле формы заявки — общее для всех ATS: answers.plan() строит по нему план, аппликатор площадки заполняет."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Field:
    """id — как найти поле в форме: в Greenhouse это id элемента, в Lever — атрибут name,
    в Ashby — data-field-path. type — словарь Greenhouse, им пользуются все площадки:
    input_text | input_file | textarea | multi_value_single_select | multi_value_multi_select."""
    id: str
    label: str
    required: bool
    type: str
    options: list[str] = field(default_factory=list)
    std: str = ""     # стандартное поле: first_name | last_name | full_name | email | phone | resume | cover_letter | location
    widget: str = ""  # как нарисовано, если это важно для заполнения: radio | checkbox | dropdown | boolean | combobox

    @property
    def key(self) -> str:
        return self.std or self.id

    @property
    def is_select(self) -> bool:
        return self.type.startswith("multi_value")

    @property
    def multi(self) -> bool:
        return self.type == "multi_value_multi_select"
