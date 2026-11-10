"""Lever Postings API: api.lever.co/v0/postings/<site>?mode=json (у фирм на EU-инстансе — api.eu.lever.co)."""
from __future__ import annotations

from typing import Any

from ..jobs import Job, html_to_text
from .http import SourceError, get_json

API = "https://api.lever.co/v0/postings/{site}"


def _job(j: dict[str, Any], firm: str, token: str) -> Job:
    cat = j.get("categories") or {}
    parts = [j.get("descriptionPlain") or "", j.get("descriptionBodyPlain") or ""]
    for block in j.get("lists") or []:
        parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content', ''))}")
    parts.append(j.get("additionalPlain") or "")
    salary = j.get("salaryRange") or {}
    comp = f"{salary.get('min')}–{salary.get('max')} {salary.get('currency', '')}".strip() if salary.get("min") else ""
    locs = cat.get("allLocations") or [cat.get("location") or ""]
    return Job(
        firm=firm, ats="lever", token=token, ext_id=str(j["id"]),
        title=(j.get("text") or "").strip(),
        location="; ".join(x for x in locs if x),
        url=j.get("hostedUrl") or "",
        apply_url=j.get("applyUrl") or j.get("hostedUrl") or "",
        department=" / ".join(x for x in (cat.get("department"), cat.get("team")) if x),
        description="\n\n".join(p.strip() for p in parts if p and p.strip()),
        compensation=comp,
        published_at=str(j.get("createdAt") or ""),
        hints=[x for x in (cat.get("commitment"),) if x],
    )


def parse(data: Any, firm: str, token: str) -> list[Job]:
    if not isinstance(data, list):
        raise SourceError(f"lever/{token}: ответ не список вакансий")
    return [_job(j, firm, token) for j in data]


def fetch(firm: str, token: str) -> list[Job]:
    return parse(get_json(API.format(site=token) + "?mode=json"), firm, token)


def fetch_one(firm: str, token: str, ext_id: str) -> Job:
    return _job(get_json(API.format(site=token) + f"/{ext_id}?mode=json"), firm, token)


# ── Форма заявки (jobs.lever.co/<site>/<id>/apply) ───────────────────
# API вопросов у Lever нет: форма — обычный HTML, разбираем его. Каждый вопрос — <li class="application-question">
# с подписью .application-label и полем (input / textarea / select; варианты — radio или checkbox).
# Поле в форме находится по атрибуту name: «name», «email», «urls[LinkedIn]», «cards[<id>][field0]».

from html.parser import HTMLParser  # noqa: E402

from .form import Field  # noqa: E402
from .http import get  # noqa: E402

FORM = "https://jobs.lever.co/{site}/{id}/apply"
# стандартные поля Lever → ключ профиля
_STD = {"name": "full_name", "email": "email", "phone": "phone", "resume": "resume", "location": "location",
        "comments": "cover_letter"}


class _FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.questions: list[dict] = []
        self._q: dict | None = None
        self._li = 0            # глубина вложенных <li>
        self._q_li = 0          # глубина <li> текущего вопроса
        self._label = 0         # внутри .application-label (глубина div)
        self._option: str | None = None  # внутри <option> выпадающего списка

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class") or ""
        if tag == "li":
            self._li += 1
            if "application-question" in cls.split():
                self._q = {"label": "", "inputs": [], "options": [], "select": False}
                self._q_li = self._li
            return
        if self._q is None:
            return
        if tag == "div" and ("application-label" in cls.split() or self._label):
            self._label += 1
        elif tag in ("input", "textarea", "select") and a.get("name") and a.get("type") != "hidden":
            self._q["inputs"].append({"tag": tag, "type": a.get("type") or tag, "name": a["name"],
                                      "value": a.get("value") or "", "required": "required" in a})
            if a.get("type") in ("radio", "checkbox") and a.get("value"):
                self._q["options"].append(a["value"])
            if tag == "select":
                self._q["select"] = True
        elif tag == "option" and self._q["select"]:
            self._option = a.get("value") or ""

    def handle_endtag(self, tag):
        if tag == "li":
            if self._q is not None and self._li == self._q_li:
                self.questions.append(self._q)
                self._q = None
            self._li -= 1
        elif tag == "div" and self._label:
            self._label -= 1
        elif tag == "option":
            self._option = None

    def handle_data(self, data):
        if self._q is None:
            return
        if self._label:
            self._q["label"] += data
        elif self._option is not None and data.strip() and self._option:
            self._q["options"].append(" ".join(data.split()))


def parse_form(html: str) -> list[Field]:
    """Поля формы Lever. Анкета EEO (eeo[…]) необязательна — не заполняем, как и в Greenhouse."""
    p = _FormParser()
    p.feed(html)
    out = []
    for q in p.questions:
        if not q["inputs"]:
            continue
        first = q["inputs"][0]
        name = first["name"]
        if name.startswith("eeo["):
            continue
        raw = " ".join(q["label"].split())
        required = "✱" in raw or any(i["required"] for i in q["inputs"])
        label = raw.replace("✱", "").strip()
        kind = first["type"]
        widget = ""
        if kind == "radio":
            ftype, widget = "multi_value_single_select", "radio"
        elif kind == "checkbox":
            ftype, widget = "multi_value_multi_select", "checkbox"
        elif first["tag"] == "select":
            ftype, widget = "multi_value_single_select", "dropdown"
        elif kind == "file":
            ftype = "input_file"
        elif first["tag"] == "textarea":
            ftype = "textarea"
        else:
            ftype = "input_text"
        out.append(Field(id=name, label=label, required=required, type=ftype, options=q["options"],
                         std=_STD.get(name, ""), widget=widget))
    if not any(f.std == "email" for f in out):
        raise SourceError("lever: в форме нет поля email — разметка поменялась?")
    return out


def form_url(token: str, ext_id: str) -> str:
    return FORM.format(site=token, id=ext_id)


def questions(token: str, ext_id: str) -> list[Field]:
    return parse_form(get(form_url(token, ext_id)).decode("utf-8", "replace"))
