"""Workable: публичный виджет доски apply.workable.com/api/v1/widget/accounts/<account>?details=true
(все вакансии с полными текстами одним запросом) и форма заявки apply.workable.com/api/v1/jobs/<shortcode>/form.

Форма (apply.workable.com/<account>/j/<shortcode>/apply/): у каждого поля контейнер [data-ui=<id>], а у текстовых
полей ещё и name=<id>. Поле Address Workable заполняет сам по IP (через VPN — чужой город): агент
всегда перезаписывает его городом из profile.city."""
from __future__ import annotations

from typing import Any

from ..jobs import Job, html_to_text
from .form import Field
from .http import SourceError, get_json

WIDGET = "https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
JOB = "https://apply.workable.com/api/v2/accounts/{token}/jobs/{ext_id}"
FORM = "https://apply.workable.com/api/v1/jobs/{ext_id}/form"


def _location(j: dict[str, Any]) -> str:
    locs = [x for x in j.get("locations") or [] if isinstance(x, dict) and not x.get("hidden")]
    if not locs:
        locs = [j.get("location") or {"city": j.get("city"), "country": j.get("country")}]
    out = [", ".join(x for x in (loc.get("city"), loc.get("country")) if x) for loc in locs]
    place = "; ".join(dict.fromkeys(x for x in out if x))
    remote = j.get("telecommuting") in (True, "True", "true") or j.get("remote") is True
    return f"{place}; Remote" if remote and place else ("Remote" if remote else place)


def _job(j: dict[str, Any], firm: str, token: str) -> Job:
    code = str(j["shortcode"])
    dept = j.get("department")
    text = "\n\n".join(html_to_text(j.get(k) or "") for k in ("description", "requirements", "benefits"))
    return Job(
        firm=firm, ats="workable", token=token, ext_id=code,
        title=(j.get("title") or "").strip(),
        location=_location(j),
        url=j.get("shortlink") or j.get("url") or f"https://apply.workable.com/{token}/j/{code}/",
        apply_url=form_url(token, code),
        department=", ".join(dept) if isinstance(dept, list) else (dept or ""),
        description=text.strip(),
        published_at=j.get("published_on") or j.get("published") or "",
        hints=[x for x in (j.get("employment_type"),) if x],
    )


def parse(data: Any, firm: str, token: str) -> list[Job]:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError(f"workable/{token}: в ответе нет списка jobs")
    return [_job(j, firm, token) for j in data["jobs"] if j.get("shortcode")]


def fetch(firm: str, token: str) -> list[Job]:
    return parse(get_json(WIDGET.format(token=token)), firm, token)


def fetch_one(firm: str, token: str, ext_id: str) -> Job:
    if not token:
        raise SourceError(f"workable: ссылка без аккаунта фирмы ({ext_id}) — укажите фирму из firms.yaml")
    return _job(get_json(JOB.format(token=token, ext_id=ext_id)), firm, token)


# ── Форма заявки ─────────────────────────────────────────────────────

_STD = {"firstname": "first_name", "lastname": "last_name", "email": "email", "phone": "phone",
        "address": "location", "resume": "resume", "cover_letter": "cover_letter"}
_TEXT = {"text": "input_text", "email": "input_text", "phone": "input_text", "number": "input_text",
         "paragraph": "textarea", "file": "input_file"}
SKIP = {"avatar"}  # фото — необязательное, не загружаем
GDPR_LABEL = "I have read and agree to the data privacy policy (GDPR consent)"


def parse_form(data: Any) -> list[Field]:
    """Поля формы. Блоки «Education» / «Experience» (group) и даты — только необязательные: обязательный такой
    блок агент заполнять не умеет, и заявка не готовится (SourceError), а не отправляется наполовину."""
    if not isinstance(data, list):
        raise SourceError(f"workable: в ответе нет формы ({str(data)[:200]})")
    out = []
    for section in data:
        for f in section.get("fields") or []:
            fid, t, req = f.get("id") or "", f.get("type") or "", bool(f.get("required"))
            label = " ".join((f.get("label") or "").split())
            if fid in SKIP:
                continue
            if fid == "gdpr":  # согласие: один вариант, только «да»
                out.append(Field(id=fid, label=GDPR_LABEL, required=req, type="multi_value_single_select",
                                 options=["Yes"], widget="checkbox"))
                continue
            if t in _TEXT:
                out.append(Field(id=fid, label=label, required=req, type=_TEXT[t], std=_STD.get(fid, "")))
            elif t == "boolean":
                out.append(Field(id=fid, label=label, required=req, type="multi_value_single_select",
                                 options=["Yes", "No"], widget="boolean"))
            elif t in ("dropdown", "multiple"):
                multi = t == "multiple" and not f.get("singleOption", True)
                options = [str(o.get("value") or "").strip() for o in f.get("options") or []]
                out.append(Field(id=fid, label=label, required=req, options=options,
                                 type="multi_value_multi_select" if multi else "multi_value_single_select",
                                 widget="dropdown" if t == "dropdown" else ("checkbox" if multi else "radio")))
            elif req:
                raise SourceError(f"workable: обязательное поле «{label or fid}» типа {t} агент заполнять не умеет")
    return out


def questions(token: str, ext_id: str) -> list[Field]:
    return parse_form(get_json(FORM.format(ext_id=ext_id)))


def form_url(token: str, ext_id: str) -> str:
    return f"https://apply.workable.com/{token}/j/{ext_id}/apply/"
