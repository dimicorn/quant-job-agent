"""Заполнение формы в настоящем Chromium на поддельной странице Greenhouse (page.route, без сети).
Разметка повторяет настоящую форму: id полей как в API, выпадающие списки — комбобоксы с .select__option."""
import json

import pytest

from quant_agent import autopilot
from quant_agent.answers import Plan, Step
from quant_agent.applier import Applier, FormError
from quant_agent.config import Education

OPTIONS = {
    "country": ["Russia +7", "Romania +40"],  # как в настоящей форме: страна с телефонным кодом
    "school--0": ["Moscow Institute Of Physics And Technology", "Aalto University"],  # регистр как у Squarepoint
    "degree--0": ["Bachelor's Degree", "Master's Degree"],
    "discipline--0": ["Computer Science", "Physics"],
    "start-month--0": ["January", "September"], "end-month--0": ["June", "December"],
    "question_1": ["Yes", "No"],
    "candidate-location": ["Moscow, Moscow City, Russia", "Moscow, Idaho, United States"],
    "question_2[]": ["Mathematics", "Physics", "Computer Science"],
}

COMBO = '<div class="select"><div class="select__control"><span class="v"></span><input id="{id}" role="combobox"></div></div>'
FORM = f"""<!doctype html><html><body><h1>Job Application</h1><form>
<input id="first_name"><input id="last_name"><input id="email"><input id="phone" type="tel">
<script>// как intl-tel-input: код страны уходит в выбор страны, номер — с разделителями
document.addEventListener('DOMContentLoaded', () => document.querySelector('#phone').addEventListener('blur', e => {{
  const d = e.target.value.replace(/\\D/g, ''); if (d.length > 6) e.target.value = '+' + d.slice(0, 1) + ' ' + d.slice(1, 4) + ' ' + d.slice(4, 7) + '-' + d.slice(7, 9) + '-' + d.slice(9); }}));</script>
{COMBO.format(id="country")}
<input id="resume" type="file">
{COMBO.format(id="school--0")}{COMBO.format(id="degree--0")}{COMBO.format(id="discipline--0")}
{COMBO.format(id="start-month--0")}<input id="start-year--0" type="number">
{COMBO.format(id="end-month--0")}<input id="end-year--0" type="number">
{COMBO.format(id="candidate-location")}{COMBO.format(id="question_1")}{COMBO.format(id="question_2[]")}
<textarea id="question_3"></textarea>
<fieldset id="question_4[]"><label><input type="checkbox" name="question_4[]" id="question_4[]_1" value="1">London</label>
<label><input type="checkbox" name="question_4[]" id="question_4[]_2" value="2">Paris</label>
<label><input type="checkbox" name="question_4[]" id="question_4[]_3" value="3">Zug</label></fieldset>
<input id="cover_letter" type="file">
<button type="submit">Submit application</button></form>
<script>
const OPTIONS = {json.dumps(OPTIONS)};
const closeAll = () => document.querySelectorAll('.select__menu').forEach(m => m.remove());
document.querySelectorAll('input[role=combobox]').forEach(inp => {{
  const ctl = inp.closest('.select__control'), shown = ctl.querySelector('.v'), multi = inp.id.endsWith('[]');
  const show = () => {{
    closeAll();
    const menu = document.createElement('div'); menu.className = 'select__menu';
    (OPTIONS[inp.id] || []).filter(o => o.toLowerCase().includes(inp.value.toLowerCase())).forEach(o => {{
      const d = document.createElement('div'); d.className = 'select__option'; d.setAttribute('role', 'option');
      d.textContent = o;
      d.addEventListener('mousedown', e => {{ e.preventDefault();
        shown.textContent = multi && shown.textContent ? shown.textContent + ', ' + o : o; inp.value = ''; closeAll(); }});
      menu.appendChild(d);
    }});
    ctl.parentElement.appendChild(menu);
  }};
  inp.addEventListener('click', show); inp.addEventListener('input', show);
  inp.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeAll(); }});
  inp.addEventListener('blur', () => setTimeout(() => {{ const m = ctl.parentElement.querySelector('.select__menu'); if (m) m.remove(); }}, 100));
}});
document.querySelector('button[type=submit]').addEventListener('click', e => {{
  e.preventDefault(); window.SUBMITTED = true; location.href = '/confirmation'; }});
</script></body></html>"""
DONE = "<html><body><h1>Thank you for applying!</h1></body></html>"


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.route("https://job-boards.greenhouse.io/**", lambda r: r.fulfill(
            content_type="text/html", body=DONE if "confirmation" in r.request.url else FORM))
        yield pg
        b.close()


@pytest.fixture
def fast(cfg, tmp_path):
    a = cfg.apply
    a.read_seconds, a.action_seconds, a.typing_delay_ms, a.delay_seconds = (0, 0), (0, 0), (0, 0), (0, 0)
    a.submit_wait_minutes = 0
    p = cfg.profile
    p.first_name, p.last_name, p.email, p.phone, p.country = "Ivan", "Petrov", "d@example.com", "+7 900 123 4567", "Russia"
    p.education = [Education(school="Moscow Institute of Physics and Technology",
                             degree="Master's Degree", discipline="Computer Science", start=(9, 2024), end=(6, 2026))]
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    return cfg, cv


def make_plan(cv, q1="No") -> Plan:
    return Plan([
        Step("first_name", "First Name", "text", ["Ivan"], "profile"),
        Step("last_name", "Last Name", "text", ["Petrov"], "profile"),
        Step("email", "Email", "text", ["d@example.com"], "profile"),
        Step("phone", "Phone", "text", ["+7 900 123 4567"], "profile"),
        Step("resume", "Resume/CV", "file", [str(cv)], "resume"),
        Step("location", "Location", "location", ["Moscow, Moscow City, Russia"], "profile"),
        Step("question_1", "Applied before?", "select", [q1], "answers"),
        Step("question_2[]", "Disciplines", "multiselect", ["Physics", "Computer Science"], "llm"),
        Step("question_3", "Anything else?", "textarea", ["Nothing to add."], "llm"),
        Step("question_4[]", "Preferred office", "multiselect", ["London", "Zug"], "answers"),  # чекбоксы
        Step("cover_letter", "Cover Letter", "file", ["Dear team,\nI am applying."], "letter"),  # PDF при открытом браузере
    ])


URL = "https://job-boards.greenhouse.io/embed/job_app?for=optiverus&token=1"


def test_fill_verify_submit_auto(page, fast, tmp_path):
    cfg, cv = fast
    ap = Applier(page, cfg, "auto")
    ap.open(URL)
    ap.fill(make_plan(cv), tmp_path)
    assert ap.verify(make_plan(cv)) == []
    assert page.locator("#school--0").evaluate("e => e.closest('.select__control').innerText").startswith("Moscow")
    assert page.locator("#end-year--0").input_value() == "2026"
    assert page.locator("#phone").input_value() == "+7 900 123-45-67"  # переформатирован, но проверка прошла
    assert page.locator("#resume").evaluate("e => e.files[0].name") == "cv.pdf"
    assert page.locator("#cover_letter").evaluate("e => e.files[0].name") == "cover_letter.pdf"
    boxes = page.locator('input[name="question_4[]"]')
    assert [boxes.nth(i).is_checked() for i in range(3)] == [True, False, True]
    assert ap.submit("Optiver — QR") == ("applied", "отправлено автоматически")


def test_school_alias(page, fast, tmp_path):
    cfg, cv = fast
    cfg.profile.education[0].school = "MIPT"  # такого варианта нет — подойдёт запасное название
    cfg.profile.education[0].school_aliases = ["Moscow Institute of Physics and Technology"]
    cfg.profile.education[0].degree, cfg.profile.education[0].degree_aliases = "Master", ["Master's Degree"]
    ap = Applier(page, cfg, "dry_run")
    ap.open(URL)
    ap.fill(make_plan(cv), tmp_path)
    assert page.locator("#school--0").evaluate("e => e.closest('.select__control').innerText").startswith("Moscow")


def test_dry_run_does_not_submit(page, fast, tmp_path):
    cfg, cv = fast
    ap = Applier(page, cfg, "dry_run")
    ap.open(URL)
    ap.fill(make_plan(cv), tmp_path)
    status, note = ap.submit("Optiver — QR")
    assert status == "dry_run" and note.endswith(".png")
    assert page.evaluate("window.SUBMITTED") is None and "confirmation" not in page.url


def test_assist_waits_for_human_and_gives_up(page, fast, tmp_path, monkeypatch):
    cfg, cv = fast
    monkeypatch.setattr("quant_agent.applier.notify", lambda *a: None)
    ap = Applier(page, cfg, "assist")
    ap.open(URL)
    ap.fill(make_plan(cv), tmp_path)
    assert ap.submit("Optiver — QR")[0] == "manual"  # человек не нажал Submit — заявка не уходит сама
    assert page.evaluate("window.SUBMITTED") is None


def test_unknown_option_is_form_error(page, fast, tmp_path):
    cfg, cv = fast
    ap = Applier(page, cfg, "auto")
    ap.open(URL)
    with pytest.raises(FormError, match="нет варианта"):
        ap.fill(make_plan(cv, q1="Maybe"), tmp_path)


def test_apply_one_records_status(page, fast, db, monkeypatch):
    cfg, cv = fast
    monkeypatch.setattr(autopilot.greenhouse, "form_url", lambda t, e: URL)
    db.seen("greenhouse:optiverus:1", firm="Optiver", ats="greenhouse", token="optiverus", ext_id="1", title="QR")
    db.update("greenhouse:optiverus:1", status="scored", score=90)
    assert autopilot.apply_one(cfg, db, page, db.get("greenhouse:optiverus:1"), make_plan(cv), "auto") == "applied"
    row = db.get("greenhouse:optiverus:1")
    assert row["status"] == "applied" and row["applied_at"]
    assert db.applied_today() == 1
