"""Разбор ответов публичных API Greenhouse, Lever, Ashby (сохранены с живых досок, обрезаны) и ссылок."""
import pytest

from quant_agent.jobs import classify_kind
from quant_agent.sources import ashby, detect, greenhouse, lever, parse_page
from quant_agent.sources.http import SourceError

from .conftest import fixture


def test_greenhouse_parse():
    jobs = greenhouse.parse(fixture("greenhouse_jobs.json"), "Optiver", "optiverus")
    assert len(jobs) == 4
    j = jobs[0]
    assert j.uid == f"greenhouse:optiverus:{j.ext_id}"
    assert j.title == "FPGA Internship (2027 Start)"
    assert "Amsterdam" in j.location
    assert j.url.startswith("https://") and j.apply_url
    assert "Campus" in j.hints
    assert "<" not in j.description and "&lt;" not in j.description and len(j.description) > 200
    assert [classify_kind(x.title, x.hints) for x in jobs] == ["internship", "graduate", "full_time", "internship"]


def test_lever_parse():
    jobs = lever.parse(fixture("lever_postings.json"), "Belvedere", "belvederetrading")
    assert len(jobs) == 2
    j = jobs[0]
    assert j.ats == "lever" and j.ext_id == "fd619115-ced7-4d59-bd2b-0948b5ee5fde"
    assert j.location == "Chicago, Illinois"
    assert j.apply_url.endswith("/apply")
    assert j.compensation.startswith("70000–90000")
    assert "Belvedere" in j.description


def test_ashby_parse_skips_unlisted():
    data = fixture("ashby_board.json")
    data["jobs"][2]["isListed"] = False
    jobs = ashby.parse(data, "Voleon", "voleon")
    assert [j.title for j in jobs] == ["Senior Member of Research Staff (London)", "Data Scientist (London)"]
    assert jobs[0].location.startswith("London")
    assert jobs[0].apply_url.endswith("/application")


@pytest.mark.parametrize("mod, bad", [(greenhouse, {"error": "x"}), (lever, {"ok": False}), (ashby, [])])
def test_unexpected_payload_is_source_error(mod, bad):
    with pytest.raises(SourceError):
        mod.parse(bad, "F", "t")


@pytest.mark.parametrize("url, expected", [
    ("https://boards.greenhouse.io/janestreet/jobs/123456", ("greenhouse", "janestreet", "123456")),
    ("https://job-boards.greenhouse.io/imc/jobs/42?gh_src=x", ("greenhouse", "imc", "42")),
    ("https://www.optiver.com/join-us/jobs/8644364002/?gh_jid=8644364002", ("greenhouse", "", "8644364002")),
    ("https://jobs.lever.co/belvederetrading/fd619115-ced7/apply", ("lever", "belvederetrading", "fd619115-ced7")),
    ("https://jobs.ashbyhq.com/voleon/702c3699", ("ashby", "voleon", "702c3699")),
    ("https://example.com/careers/quant", None),
])
def test_detect(url, expected):
    assert detect(url) == expected


def test_parse_page_json_ld():
    page = """<html><head><title>Careers</title><script type="application/ld+json">
    {"@context": "https://schema.org", "@graph": [{"@type": "Organization"}, {"@type": "JobPosting",
     "title": "Quantitative Researcher &ndash; Graduate", "description": "<p>We use <b>statistics</b>.</p>",
     "hiringOrganization": {"@type": "Organization", "name": "Acme Capital"}, "identifier": {"value": "QR-7"},
     "employmentType": "FULL_TIME", "datePosted": "2026-10-01",
     "jobLocation": {"@type": "Place", "address": {"addressLocality": "London", "addressCountry": "GB"}}}]}
    </script></head><body>ignored</body></html>"""
    j = parse_page(page, "https://acme.example/jobs/qr")
    assert (j.firm, j.title, j.ext_id, j.location) == ("Acme Capital", "Quantitative Researcher – Graduate", "QR-7", "London, GB")
    assert j.description == "We use statistics."


def test_parse_page_without_markup_falls_back_to_text():
    j = parse_page("<html><title>Quant Intern</title><script>x=1</script><body><p>Hello</p></body></html>",
                   "https://firm.example/j/1")
    assert j.title == "Quant Intern" and "Hello" in j.description and "x=1" not in j.description


def test_lever_form_parse():
    """Настоящая форма Wintermute: стандартные поля, ссылки, карточки вопросов с вариантами-радиокнопками."""
    from .conftest import FIXTURES
    fields = lever.parse_form((FIXTURES / "lever_form.html").read_text(encoding="utf-8"))
    by = {f.id: f for f in fields}
    assert [f.std for f in fields[:5]] == ["resume", "full_name", "email", "phone", "location"]
    assert by["name"].required and not by["phone"].required and by["resume"].type == "input_file"
    assert by["urls[LinkedIn]"].label == "LinkedIn URL"
    py = next(f for f in fields if f.label.startswith("Please describe your level of Python"))
    assert py.is_select and py.widget == "radio" and py.required
    assert py.options == ["Advance", "Intermediate", "Basic", "Not used before, but happy to learn"]
    assert py.id.startswith("cards[") and py.id.endswith("][field1]")
    assert not any(f.id.startswith("eeo[") for f in fields)
    with pytest.raises(SourceError):
        lever.parse_form("<html><form><li class='application-question'><input name='x'></li></form></html>")


def test_ashby_form_parse():
    """Настоящая форма Voleon (GraphQL): стандартные поля по path, «да/нет», город, скрытые поля пропущены."""
    fields = ashby.parse_form(fixture("ashby_form.json"))
    by = {f.label: f for f in fields}
    assert by["Full Name"].std == "full_name" and by["Email"].std == "email" and by["Resume"].std == "resume"
    assert by["Phone Number"].std == "phone" and not by["Phone Number"].required
    assert by["Current Location"].std == "location" and by["Current Location"].widget == "combobox"
    yes = by["Are you legally authorized to work in the United States?"]
    assert yes.is_select and yes.options == ["Yes", "No"] and yes.widget == "boolean" and yes.required
    assert by["If you require sponsorship now or in the future, please provide more detail."].type == "textarea"
    with pytest.raises(SourceError):
        ashby.parse_form({"data": {"jobPosting": None}})


SITE = """<html><head><title>Careers</title></head><body><nav><a href="/jobs/">All jobs</a></nav>
<a href="/jobs/junior-c++-engineer">Junior C++</a><a href="https://wunderfund.io/jobs/hft-quant">HFT</a>
<a href="/jobs/junior-c++-engineer">дубль</a><a href="https://other.com/jobs/x">чужой сайт</a><a href="/about">О нас</a>
<script>var x = 1;</script><p>Open positions: ML Engineer - Tbilisi</p></body></html>"""


def test_site_job_links_and_listing():
    from quant_agent.sources import site
    assert site.job_links(SITE, "https://wunderfund.io/", r"^/jobs/[^/]+") == [
        "https://wunderfund.io/jobs/junior-c++-engineer", "https://wunderfund.io/jobs/hft-quant"]
    a = site.parse_listing(SITE, "Quantum Brains", "https://quantumbrains.com/careers")
    b = site.parse_listing(SITE.replace("ML Engineer", "C++ Engineer"), "Quantum Brains", "https://quantumbrains.com/careers")
    assert a.ats == "site" and "ML Engineer - Tbilisi" in a.description and "var x" not in a.description
    assert a.uid != b.uid  # поменялся список позиций — новая запись, снова на оценку


def test_site_fetch_pages(monkeypatch):
    from quant_agent.sources import site
    pages = {"https://wunderfund.io/": SITE,
             "https://wunderfund.io/jobs/junior-c++-engineer": "<title>Junior C++ Engineer @ Wunder Fund</title><p>C++20</p>",
             "https://wunderfund.io/jobs/hft-quant": "<title>HFT Quant</title><p>stats</p>"}
    monkeypatch.setattr(site, "get", lambda u: pages[u].encode())
    jobs = site.fetch("Wunder Fund", "https://wunderfund.io/", r"^/jobs/[^/]+")
    assert [(j.firm, j.ext_id, j.title) for j in jobs] == [
        ("Wunder Fund", "/jobs/junior-c++-engineer", "Junior C++ Engineer @ Wunder Fund"),
        ("Wunder Fund", "/jobs/hft-quant", "HFT Quant")]
    with pytest.raises(SourceError):
        site.fetch("Wunder Fund", "https://wunderfund.io/", r"^/vacancies/")


SIMPLIFY = [
    {"category": "Quant", "company_name": "Jump Trading", "id": "a1", "title": "Campus Quantitative Researcher Intern",
     "active": True, "is_visible": True, "terms": ["Summer 2027"], "date_posted": 1788000000,
     "url": "https://boards.greenhouse.io/embed/job_app?token=8010307&utm_source=Simplify&ref=Simplify",
     "locations": ["London, UK"]},
    {"category": "Quant", "company_name": "Susquehanna International Group (SIG)", "id": "b2", "title": "Quant Research Intern",
     "active": True, "is_visible": True, "terms": ["Summer 2027"], "url": "https://careers-sig.icims.com/jobs/11007/job",
     "locations": ["London, UK", "Dublin, Ireland"]},
    {"category": "Quant", "company_name": "Optiver", "id": "c3", "title": "QR Graduate", "active": True,
     "url": "https://www.optiver.com/join-us/jobs/8585609002/?gh_jid=8585609002", "locations": ["London, UK"]},
    {"category": "Quant", "company_name": "Old Co", "id": "d4", "title": "Closed", "active": False, "url": "https://x.io/1",
     "locations": []},
    {"category": "Software", "company_name": "Jump Trading", "id": "e5", "title": "SWE Intern", "active": True,
     "url": "https://x.io/2", "locations": []},
]


def test_simplify_parse_maps_to_firm_boards():
    from quant_agent.firms import Firm
    from quant_agent.sources import simplify
    firms = [Firm(name="Jump Trading", ats="greenhouse", token="jumptrading"),
             Firm(name="Optiver", ats="greenhouse", token="optiverus")]
    jobs = simplify.parse(SIMPLIFY, firms, "Internship")
    assert [j.uid for j in jobs] == ["greenhouse:jumptrading:8010307", "page:careers-sig.icims.com:b2",
                                     "greenhouse:optiverus:8585609002"]  # закрытые и не Quant — мимо
    assert jobs[0].firm == "Jump Trading" and "utm_source" not in jobs[0].url
    assert jobs[1].firm == "Susquehanna International Group (SIG)" and jobs[1].location == "London, UK; Dublin, Ireland"
    assert "Internship" in jobs[1].hints and jobs[0].published_at.startswith("2026")
    assert simplify.match_firm("Five Rings Capital", [Firm(name="Five Rings", ats="greenhouse", token="f")]).name == "Five Rings"
    with pytest.raises(SourceError):
        simplify.parse({"x": 1}, firms, "Internship")


def test_detect_greenhouse_embed():
    assert detect("https://boards.greenhouse.io/embed/job_app?token=8010307") == ("greenhouse", "", "8010307")
    assert detect("https://job-boards.greenhouse.io/embed/job_app?for=mako&token=1") == ("greenhouse", "mako", "1")


def test_match_firm_empty_name():
    """Пустое название компании не должно совпадать с первой фирмой списка."""
    from quant_agent.firms import Firm
    from quant_agent.sources import simplify
    firms = [Firm(name="Optiver", ats="greenhouse", token="o"), Firm(name="Five Rings", ats="greenhouse", token="f")]
    assert simplify.match_firm("", firms) is None and simplify.match_firm("  ", firms) is None
