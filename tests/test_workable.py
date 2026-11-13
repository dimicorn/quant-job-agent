"""Workable: доска (виджет), форма (API) и заполнение формы с разметкой как на apply.workable.com.
Фикстуры — настоящие ответы API Pinely (ноябрь 2026), обрезанные."""
import pytest

from quant_agent.answers import Plan, Step
from quant_agent.applier import FormError
from quant_agent.applier_workable import WorkableApplier
from quant_agent.sources import SourceError, detect, workable

from .conftest import fixture


def test_parse_board():
    jobs = workable.parse(fixture("workable_widget.json"), "Pinely", "pinely")
    assert [j.uid for j in jobs] == ["workable:pinely:B4F63D18E2", "workable:pinely:4B5411E50B",
                                     "workable:pinely:B2C938E416"]
    j = jobs[2]
    assert j.title == "Middle Quantitative Researcher" and j.location == "Amsterdam, Netherlands"
    assert j.apply_url == "https://apply.workable.com/pinely/j/B2C938E416/apply/"
    assert "Quantitative Researcher" in j.description and "<p>" not in j.description
    assert j.department == "Research" and j.hints == ["Full-time"]
    with pytest.raises(SourceError):
        workable.parse({"x": 1}, "Pinely", "pinely")


def test_parse_form():
    fields = {f.id: f for f in workable.parse_form(fixture("workable_form_intern.json"))}
    assert "avatar" not in fields                                    # фото не загружаем
    assert fields["firstname"].key == "first_name" and fields["address"].key == "location"
    assert fields["resume"].key == "resume" and fields["cover_letter"].key == "cover_letter"
    assert fields["CA_49337"].is_select and "Linkedin" in fields["CA_49337"].options
    assert fields["QA_10721469"].multi and fields["QA_10721469"].options == ["Internship", "Full-time position", "Both"]
    assert not fields["QA_10721471"].multi and fields["QA_10721471"].options == ["Yes", "No"]
    assert fields["QA_10721465"].type == "input_text" and fields["QA_10721464"].type == "textarea"
    g = fields["gdpr"]
    assert g.options == ["Yes"] and g.required                       # согласие: answers.plan отметит его сам
    boolean = {f.id: f for f in workable.parse_form(fixture("workable_form.json"))}["QA_10705832"]
    assert boolean.options == ["Yes", "No"] and boolean.widget == "boolean"


def test_required_group_is_not_half_filled():
    data = [{"name": "Profile", "fields": [{"id": "education", "label": "Education", "required": True, "type": "group",
                                            "fields": []}]}]
    with pytest.raises(SourceError, match="Education"):
        workable.parse_form(data)


@pytest.mark.parametrize("url, hit", [
    ("https://apply.workable.com/pinely/j/B2C938E416/", ("workable", "pinely", "B2C938E416")),
    ("https://apply.workable.com/pinely/j/B2C938E416/apply/", ("workable", "pinely", "B2C938E416")),
    ("https://apply.workable.com/j/B2C938E416", ("workable", "", "B2C938E416")),
    ("https://pinely.workable.com/j/B2C938E416", ("workable", "pinely", "B2C938E416")),
])
def test_detect(url, hit):
    assert detect(url) == hit


# ── заполнение: разметка как на apply.workable.com ──

FORM = """<!doctype html><html><body>
<div id="cookies"><button data-ui="cookie-consent-decline" onclick="this.parentElement.remove()">Decline all</button></div>
<form data-ui="application-form">
<input name="firstname" data-ui="firstname"><input name="lastname" data-ui="lastname"><input name="email" data-ui="email">
<div data-ui="phone"><ul role="listbox" hidden><li role="option">United States (+1)</li></ul>
  <input type="tel" name="phone" id="input_phone"></div>
<input name="address" data-ui="address" value="Amsterdam, Netherlands">
<input type="file" data-ui="avatar"><input type="file" data-ui="resume">
<textarea name="cover_letter" data-ui="cover_letter"></textarea>
<div data-ui="CA_1"><input id="input_CA_1_input" role="combobox" readonly>
  <ul id="input_CA_1_listbox" role="listbox" hidden><li role="option">Linkedin</li><li role="option">ML events</li></ul>
  <input name="CA_1" aria-hidden="true"></div>
<input name="QA_2" data-ui="QA_2" type="text">
<div role="group" data-ui="QA_3">
  <label><div role="checkbox" aria-checked="false" aria-labelledby="q3 c1"><input type="checkbox" aria-hidden="true"></div><span id="c1">Internship</span></label>
  <label><div role="checkbox" aria-checked="false" aria-labelledby="q3 c2"><input type="checkbox" aria-hidden="true"></div><span id="c2">Full-time position</span></label>
  <label><div role="checkbox" aria-checked="false" aria-labelledby="q3 c3"><input type="checkbox" aria-hidden="true"></div><span id="c3">Both</span></label></div>
<fieldset role="radiogroup" data-ui="QA_4">
  <label><div role="radio" aria-checked="false" aria-labelledby="q4 r1"><input type="radio" name="QA_4" value="true"></div><span id="r1">YES</span></label>
  <label><div role="radio" aria-checked="false" aria-labelledby="q4 r2"><input type="radio" name="QA_4" value="false"></div><span id="r2">NO</span></label></fieldset>
<label data-ui="gdpr"><div role="checkbox" aria-checked="false" aria-labelledby="g1"><input type="checkbox" name="gdpr"></div><span id="g1">I agree</span></label>
<button type="submit" data-ui="apply-button">Submit application</button></form>
<script>
document.addEventListener('click', e => {
  if (e.target.matches('input[type=checkbox], input[type=radio]')) return;  // повторный клик от <label>
  const r = e.target.closest('[role=radio]');
  if (r) { r.closest('[role=radiogroup]').querySelectorAll('[role=radio]').forEach(x => x.setAttribute('aria-checked', x === r)); }
  const c = e.target.closest('[role=checkbox]');
  if (c) c.setAttribute('aria-checked', c.getAttribute('aria-checked') !== 'true');
  const cb = e.target.closest('[role=combobox]');
  if (cb) document.getElementById(cb.id.replace('_input', '_listbox')).hidden = false;
  const o = e.target.closest('#input_CA_1_listbox [role=option]');
  if (o) { document.getElementById('input_CA_1_input').value = o.innerText; o.parentElement.hidden = true; }
});
</script></body></html>"""
URL = "https://apply.workable.com/pinely/j/B2C938E416/apply/"


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.route("https://apply.workable.com/**", lambda r: r.fulfill(content_type="text/html", body=FORM))
        yield pg
        b.close()


@pytest.fixture
def fast(cfg):
    a = cfg.apply
    a.read_seconds, a.action_seconds, a.typing_delay_ms = (0, 0), (0, 0), (0, 0)
    return cfg


def test_fill_and_verify(page, fast, tmp_path):
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    plan = Plan([
        Step("firstname", "First name", "text", ["Ivan"], "profile"),
        Step("phone", "Phone", "text", ["+7 900 123 4567"], "profile"),
        Step("address", "Address", "location", ["Moscow, Russia"], "profile"),
        Step("resume", "Resume", "file", [str(pdf)], "resume"),
        Step("cover_letter", "Cover letter", "textarea", ["Dear Pinely team,\nIvan"], "letter"),
        Step("CA_1", "How did you hear about us?", "select", ["Linkedin"], "answers"),
        Step("QA_2", "Year of graduation", "text", ["2026"], "llm"),
        Step("QA_3", "Internships or full-time?", "multiselect", ["Internship", "Full-time position"], "answers"),
        Step("QA_4", "Proficiency in Python.", "select", ["Yes"], "llm"),
        Step("gdpr", workable.GDPR_LABEL, "select", ["Yes"], "consent"),
    ])
    ap = WorkableApplier(page, fast, "dry_run")
    ap.open(URL)
    ap.fill(plan, tmp_path)
    assert ap.verify(plan) == []
    assert page.locator("[name=address]").input_value() == "Moscow, Russia"   # город по IP перезаписан
    assert page.locator("#cookies").count() == 0
    assert page.locator("[data-ui=QA_3] [aria-checked=true]").count() == 2
    assert page.locator("[data-ui=gdpr] [role=checkbox]").get_attribute("aria-checked") == "true"
    assert page.locator("[data-ui=resume]").evaluate("e => e.files.length") == 1


def test_missing_option_is_form_error(page, fast, tmp_path):
    ap = WorkableApplier(page, fast, "dry_run")
    ap.open(URL)
    with pytest.raises(FormError, match="нет варианта"):
        ap.fill(Plan([Step("CA_1", "How did you hear about us?", "select", ["Hackathon"], "answers")]), tmp_path)
