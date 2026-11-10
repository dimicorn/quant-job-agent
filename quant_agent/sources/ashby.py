"""Ashby Posting API: api.ashbyhq.com/posting-api/job-board/<name>?includeCompensation=true."""
from __future__ import annotations

from typing import Any

from ..jobs import Job
from .http import SourceError, get_json

API = "https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true"


def _job(j: dict[str, Any], firm: str, token: str) -> Job:
    locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
    comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
    return Job(
        firm=firm, ats="ashby", token=token, ext_id=str(j["id"]),
        title=(j.get("title") or "").strip(),
        location="; ".join(x for x in locs if x),
        url=j.get("jobUrl") or "",
        apply_url=j.get("applyUrl") or j.get("jobUrl") or "",
        department=" / ".join(x for x in (j.get("department"), j.get("team")) if x),
        description=(j.get("descriptionPlain") or "").strip(),
        compensation=comp,
        published_at=j.get("publishedAt") or "",
        hints=[x for x in (j.get("employmentType"),) if x],
    )


def parse(data: Any, firm: str, token: str) -> list[Job]:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise SourceError(f"ashby/{token}: в ответе нет списка jobs")
    return [_job(j, firm, token) for j in data["jobs"] if j.get("isListed", True)]


def fetch(firm: str, token: str) -> list[Job]:
    return parse(get_json(API.format(name=token)), firm, token)


def fetch_one(firm: str, token: str, ext_id: str) -> Job:
    for job in fetch(firm, token):  # отдельного запроса одной вакансии у публичного API нет
        if job.ext_id == ext_id:
            return job
    raise SourceError(f"ashby/{token}: вакансии {ext_id} нет на доске (закрыта?)")


# ── Форма заявки (jobs.ashbyhq.com/<name>/<id>/application) ──────────
# Вопросы — из того же GraphQL, которым пользуется страница формы. В форме поле лежит в
# контейнере [data-field-path=<path>]; по path его и находим.

from .form import Field  # noqa: E402
from .http import post_json  # noqa: E402

GRAPHQL = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
_QUERY = ("query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
          "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { "
          "id title applicationForm { id sections { title fieldEntries { ... on FormFieldEntry { "
          "id field isRequired isHidden } } } } } }")
_STD_PATH = {"_systemfield_name": "full_name", "_systemfield_email": "email", "_systemfield_resume": "resume",
             "_systemfield_location": "location"}
_TYPES = {"String": "input_text", "Email": "input_text", "Phone": "input_text", "Url": "input_text",
          "Number": "input_text", "Date": "input_text", "LongText": "textarea", "File": "input_file",
          "Boolean": "multi_value_single_select", "ValueSelect": "multi_value_single_select",
          "MultiValueSelect": "multi_value_multi_select", "Location": "input_text"}


def parse_form(data: dict[str, Any]) -> list[Field]:
    try:
        sections = data["data"]["jobPosting"]["applicationForm"]["sections"]
    except (KeyError, TypeError):
        raise SourceError(f"ashby: в ответе нет формы заявки ({str(data)[:200]})")
    out = []
    for s in sections:
        for e in s.get("fieldEntries") or []:
            f = e.get("field") or {}
            if e.get("isHidden") or not f.get("path"):
                continue
            t, path, title = f.get("type") or "String", f["path"], " ".join((f.get("title") or "").split())
            std = _STD_PATH.get(path, "")
            if not std and t == "Phone":
                std = "phone"
            if not std and t == "File" and "cover letter" in title.lower():
                std = "cover_letter"
            if not std and t == "Location":
                std = "location"
            options = ["Yes", "No"] if t == "Boolean" else [v.get("label", "").strip()
                                                            for v in f.get("selectableValues") or []]
            widget = {"Boolean": "boolean", "Location": "combobox"}.get(t, "")
            out.append(Field(id=path, label=title, required=bool(e.get("isRequired")), type=_TYPES.get(t, "input_text"),
                             options=options, std=std, widget=widget))
    return out


def form_url(token: str, ext_id: str) -> str:
    return f"https://jobs.ashbyhq.com/{token}/{ext_id}/application"


def questions(token: str, ext_id: str) -> list[Field]:
    return parse_form(post_json(GRAPHQL, {"operationName": "ApiJobPosting", "query": _QUERY, "variables": {
        "organizationHostedJobsPageName": token, "jobPostingId": ext_id}}))
