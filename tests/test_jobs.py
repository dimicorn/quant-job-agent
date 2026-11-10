"""Тип вакансии, локальные фильтры, условия допуска и ATS-скор — всё без LLM."""
import pytest

from quant_agent.config import Eligibility, Filters
from quant_agent.jobs import Job, ats_score, classify_kind, eligibility, prefilter


def job(title="Quantitative Researcher", description="", location="London"):
    return Job(firm="F", ats="greenhouse", token="f", ext_id="1", title=title, description=description, location=location)


@pytest.mark.parametrize("title, hints, kind", [
    ("Quantitative Research Intern", [], "internship"),
    ("2027 Summer Internship - Software Engineer", [], "internship"),
    ("Off-Cycle Trading Placement", [], "internship"),
    ("Graduate Quant Developer", [], "graduate"),
    ("Campus Full Time 2027 - Quantitative Trader", [], "graduate"),
    ("C++ Software Engineer, Early Careers", [], "graduate"),
    ("Quantitative Researcher", ["Campus"], "graduate"),
    ("Researcher", ["Intern"], "internship"),
    ("Senior Quantitative Developer", [], "full_time"),
    ("Junior Lead Engineer", [], "full_time"),  # junior, но lead — штат
    ("Quantitative Developer", [], "full_time"),
])
def test_classify_kind(title, hints, kind):
    assert classify_kind(title, hints) == kind


def test_prefilter():
    f = Filters(kinds=["graduate", "internship"], locations=["London", "Amsterdam"], title_exclude=["recruiter"])
    assert prefilter(job(), "graduate", f) is None
    assert "filters.kinds" in prefilter(job(), "full_time", f)
    assert "локация" in prefilter(job(location="Chicago, IL"), "graduate", f)
    assert "recruiter" in prefilter(job(title="Campus Recruiter"), "graduate", f)
    assert prefilter(job(location="Chicago; London"), "graduate", f) is None  # одна из локаций подходит
    assert prefilter(job(), "full_time", f, kinds=["full_time"]) is None     # типы вакансий фирмы из firms.yaml
    assert prefilter(job(location=""), "graduate", f) is None               # локация не указана — решит LLM


E = Eligibility(enrolled=False, graduation_year=2026, needs_sponsorship=True, restricted_countries=["Russia"])


@pytest.mark.parametrize("kind, text, reason", [
    ("internship", "You are currently pursuing a Bachelor's, Master's or PhD in a STEM field.", "студентов"),
    ("internship", "Open to students returning to university after the internship.", "студентов"),
    ("graduate", "Candidates must be currently enrolled in a degree program.", None),  # только для стажировок
    ("graduate", "You are graduating between December 2026 and 2027.", None),
    ("graduate", "Expected graduation in 2027 or 2028.", "2027–2028"),
    ("graduate", "Graduating in 2027.", "2027"),
    # реальные формулировки с досок Optiver, IMC, Aquatic (ноябрь 2026)
    ("graduate", "with a graduation date between May 2025 and February 2028.", None),
    ("graduate", "Graduating between August 2024 and August 2027, with a maximum of 3 years of work experience", None),
    ("graduate", "Recently graduated (or graduating by 2027) with a degree in Computer Science", None),
    ("internship", "Expected to graduate by Summer 2028", None),
    ("graduate", "an expected graduation date between Fall 2027 and Summer 2028.", "2027–2028"),
    ("graduate", "Graduated in 2024 or later.", None),
    ("graduate", "You must have graduated by 2025.", "не позже 2025"),
    ("internship", "Graduating year of 2028", "2028"),
    ("graduate", "We are unable to sponsor visas for this role.", "визу"),
    ("graduate", "Applicants must have the right to work in the UK.", "визу"),
    ("graduate", "Visa sponsorship is available for exceptional candidates.", None),
    ("graduate", "We are unable to consider candidates located in Russia or Belarus.", "Russia"),
    ("graduate", "We cannot accept applications from residents of sanctioned countries.", "Russia"),
    ("graduate", "Our offices in London and Amsterdam work with clients worldwide.", None),
])
def test_eligibility(kind, text, reason):
    got = eligibility(job(description=text), kind, E)
    assert (got is None) if reason is None else (reason in got), got


def test_eligibility_respects_settings():
    e = Eligibility(enrolled=True, graduation_year=2028, needs_sponsorship=False)
    text = "You are currently pursuing a PhD and graduating in 2027 or 2028. No visa sponsorship."
    assert eligibility(job(description=text), "internship", e) is None


def test_ats_score():
    desc = "Strong C++ and Python. Statistics and probability. Experience with FPGA and kdb+ is a plus."
    resume = "Python, C++17, статистика, теория вероятностей."
    score, have, missing = ats_score(desc, resume)
    assert set(have) == {"C++", "Python", "statistics", "probability"}
    assert set(missing) == {"FPGA", "kdb+/q"}
    assert score == round(100 * 4 / 6)
    assert ats_score("Nothing technical here.", resume) == (None, [], [])


def test_start_year_in_title_is_not_graduation_year():
    """«Graduate Equity Analyst (2027 Start)» у Optiver — год начала работы, а не выпуска."""
    j = job(title="Graduate Equity Analyst (2027 Start)", description="Join our 2027 graduate programme.")
    assert eligibility(j, "graduate", E) is None
