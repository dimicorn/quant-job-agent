"""Greenhouse Job Board API: boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true."""
from __future__ import annotations

from typing import Any

from ..jobs import Job, html_to_text
from .form import Field
from .http import SourceError, get_json

# Field реэкспортируется: тесты и старый код импортируют его отсюда
__all__ = ["Field", "fetch", "fetch_one", "parse", "parse_questions", "questions", "form_url"]

API = "https://boards-api.greenhouse.io/v1/boards/{token}"


def _job(j: dict[str, Any], firm: str, token: str) -> Job:
    hints = [str(m.get("value")) for m in j.get("metadata") or []
             if m.get("value") and m.get("name") in ("Workflow", "Employment Type", "Job Type", "Program")]
    return Job(
        firm=firm, ats="greenhouse", token=token, ext_id=str(j["id"]),
        title=(j.get("title") or "").strip(),
        location=((j.get("location") or {}).get("name") or "").strip(),
        url=j.get("absolute_url") or "",
        apply_url=j.get("absolute_url") or "",
        department=", ".join(d.get("name", "") for d in j.get("departments") or []),
        description=html_to_text(j.get("content") or ""),
        published_at=j.get("first_published") or j.get("updated_at") or "",
        hints=hints,
    )


def parse(data: dict[str, Any], firm: str, token: str) -> list[Job]:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError(f"greenhouse/{token}: в ответе нет списка jobs")
    return [_job(j, firm, token) for j in data["jobs"]]


def fetch(firm: str, token: str) -> list[Job]:
    return parse(get_json(API.format(token=token) + "/jobs?content=true"), firm, token)


def fetch_one(firm: str, token: str, ext_id: str) -> Job:
    return _job(get_json(API.format(token=token) + f"/jobs/{ext_id}"), firm, token)


def board_name(token: str) -> str:
    return (get_json(API.format(token=token)).get("name") or "").strip()


# ── Вопросы формы заявки (?questions=true) ───────────────────────────

def parse_questions(data: dict[str, Any]) -> list[Field]:
    """Все поля формы: обычные вопросы и вопросы о локации. resume_text / cover_letter_text — альтернатива
    загрузке файла, их пропускаем. Анкеты EEO (compliance, demographic) необязательны — не заполняем."""
    if not isinstance(data, dict) or "questions" not in data:
        raise SourceError("greenhouse: в ответе нет questions")
    out = []
    for q in (data.get("questions") or []) + (data.get("location_questions") or []):
        for f in q.get("fields") or []:
            # resume_text — альтернатива файлу; latitude/longitude (input_hidden) ставит виджет города сам
            if f.get("name") in ("resume_text", "cover_letter_text") or f.get("type") == "input_hidden":
                continue
            out.append(Field(id=f["name"], label=" ".join((q.get("label") or "").split()), required=bool(q.get("required")),
                             type=f.get("type") or "input_text", options=[v["label"] for v in f.get("values") or []]))
    return out


def questions(token: str, ext_id: str) -> list[Field]:
    return parse_questions(get_json(API.format(token=token) + f"/jobs/{ext_id}?questions=true"))


def form_url(token: str, ext_id: str) -> str:
    return f"https://job-boards.greenhouse.io/embed/job_app?for={token}&token={ext_id}"
