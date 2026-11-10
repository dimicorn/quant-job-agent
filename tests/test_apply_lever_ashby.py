"""Заполнение форм Lever и Ashby в настоящем Chromium на поддельных страницах (page.route, без сети).
Разметка повторяет настоящие формы: Lever — поля по name, радиокнопки с value, подсказки .dropdown-location;
Ashby — контейнеры [data-field-path], кнопки «да/нет», радиокнопки с <label for>, город — role=combobox/option."""
import pytest

from quant_agent import autopilot
from quant_agent.answers import Plan, Step
from quant_agent.applier import FormError, place_match
from quant_agent.applier_ashby import AshbyApplier
from quant_agent.applier_lever import LeverApplier

CARD = "cards[c1]"
LEVER = f"""<!doctype html><html><body><form id="application-form"><ul>
<li class="application-question resume"><input id="resume-upload-input" name="resume" type="file"></li>
<li class="application-question"><input type="text" name="name"></li>
<li class="application-question"><input type="email" name="email"></li>
<li class="application-question"><input type="text" name="phone"></li>
<li class="application-question"><div class="application-field"><input id="location-input" type="text" name="location">
  <div class="dropdown-results"></div></div></li>
<li class="application-question"><input type="text" name="urls[LinkedIn]"></li>
<li class="application-question custom-question"><ul>
  <li><label><input type="radio" name="{CARD}[field0]" value="Advance"><span>Advance</span></label></li>
  <li><label><input type="radio" name="{CARD}[field0]" value="Basic"><span>Basic</span></label></li></ul></li>
<li class="application-question custom-question"><ul>
  <li><label><input type="checkbox" name="{CARD}[field1]" value="London"><span>London</span></label></li>
  <li><label><input type="checkbox" name="{CARD}[field1]" value="Singapore"><span>Singapore</span></label></li>
  <li><label><input type="checkbox" name="{CARD}[field1]" value="Remote"><span>Remote</span></label></li></ul></li>
<li class="application-question custom-question"><select name="{CARD}[field2]"><option value="">Select...</option>
  <option value="a">Yes</option><option value="b">No</option></select></li>
<li class="application-question custom-question"><textarea name="{CARD}[field3]"></textarea></li>
</ul><button id="btn-submit" type="button">Submit application</button></form>
<script>
const loc = document.querySelector('#location-input'), res = document.querySelector('.dropdown-results');
loc.addEventListener('input', () => {{
  res.innerHTML = '';
  if (loc.value.length < 3) return;
  ['Moscow, ID, USA', 'Moscow, RUS', 'Moscow, PA, USA'].forEach((t, i) => {{
    const d = document.createElement('div'); d.className = 'dropdown-location'; d.id = 'location-' + i; d.textContent = t;
    d.addEventListener('click', () => {{ loc.value = t; res.innerHTML = ''; }}); res.appendChild(d); }});
}});
document.querySelector('#btn-submit').addEventListener('click', () => {{ window.SUBMITTED = true; location.href = location.href.replace('/apply', '/thanks'); }});
</script></body></html>"""
THANKS = "<html><body><h1>Application submitted!</h1></body></html>"

ASHBY = """<!doctype html><html><body><form>
<div data-field-path="_systemfield_name"><label for="_systemfield_name">Name</label><input id="_systemfield_name"></div>
<div data-field-path="_systemfield_email"><input id="_systemfield_email" type="email"></div>
<div data-field-path="_systemfield_resume"><input id="_systemfield_resume" type="file"></div>
<div data-field-path="p_phone"><input id="p_phone" type="tel"></div>
<div data-field-path="_systemfield_location"><input role="combobox" placeholder="Start typing..."><div class="popup"></div></div>
<div data-field-path="q_bool"><label>Authorized?</label><div>
  <button type="button" data-option="yes" aria-pressed="false">Yes</button>
  <button type="button" data-option="no" aria-pressed="false">No</button><input type="checkbox" name="q_bool" tabindex="-1"></div></div>
<div data-field-path="q_radio"><fieldset><label class="ashby-application-form-question-title">Gender</label>
  <div><input type="radio" id="r0" name="q_radio" style="opacity:0;position:absolute"><label for="r0">Male </label></div>
  <div><input type="radio" id="r1" name="q_radio" style="opacity:0;position:absolute"><label for="r1">Prefer not to say</label></div>
</fieldset></div>
<div data-field-path="q_multi"><div><input type="checkbox" id="m0"><label for="m0">Python</label></div>
  <div><input type="checkbox" id="m1"><label for="m1">C++</label></div><div><input type="checkbox" id="m2"><label for="m2">Rust</label></div></div>
<div data-field-path="q_long"><textarea id="q_long"></textarea></div>
<button type="button" id="go">Submit Application</button></form>
<script>
const c = document.querySelector('[data-field-path="_systemfield_location"] input'), pop = c.nextElementSibling;
c.addEventListener('input', () => { pop.innerHTML = '';
  ['Moscow, Russia', 'Moscow Oblast, Russia', 'Moscow, Idaho, United States'].forEach(t => {
    const d = document.createElement('div'); d.setAttribute('role', 'option'); d.textContent = t;
    d.addEventListener('click', () => { c.value = t; pop.innerHTML = ''; }); pop.appendChild(d); }); });
document.querySelectorAll('button[data-option]').forEach(b => b.addEventListener('click', () => {
  b.parentElement.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', 'false'));
  b.setAttribute('aria-pressed', 'true'); }));
document.querySelector('#go').addEventListener('click', () => { window.SUBMITTED = true;
  document.body.innerHTML = '<p>Your application was successfully submitted. We will be in touch.</p>'; });
</script></body></html>"""

LEVER_URL = "https://jobs.lever.co/wintermute-trading/abc/apply"
ASHBY_URL = "https://jobs.ashbyhq.com/wincent/abc/application"


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.route("https://jobs.lever.co/**", lambda r: r.fulfill(
            content_type="text/html", body=THANKS if r.request.url.endswith("/thanks") else LEVER))
        pg.route("https://jobs.ashbyhq.com/**", lambda r: r.fulfill(content_type="text/html", body=ASHBY))
        yield pg
        b.close()


@pytest.fixture
def fast(cfg, tmp_path):
    a = cfg.apply
    a.read_seconds, a.action_seconds, a.typing_delay_ms, a.delay_seconds = (0, 0), (0, 0), (0, 0), (0, 0)
    a.submit_wait_minutes = 0
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    return cfg, cv


def lever_plan(cv, level="Advance") -> Plan:
    return Plan([
        Step("resume", "Resume/CV", "file", [str(cv)], "resume"),
        Step("name", "Full name", "text", ["Ivan Petrov"], "profile"),
        Step("email", "Email", "text", ["d@example.com"], "profile"),
        Step("phone", "Phone", "text", ["+7 900 123 4567"], "profile"),
        Step("location", "Current location", "location", ["Moscow, Moscow City, Russia"], "profile"),
        Step("urls[LinkedIn]", "LinkedIn URL", "text", ["linkedin.com/in/d"], "profile"),
        Step(f"{CARD}[field0]", "Python level", "select", [level], "llm"),
        Step(f"{CARD}[field1]", "Offices", "multiselect", ["London", "Remote"], "answers"),
        Step(f"{CARD}[field2]", "Authorized?", "select", ["No"], "llm"),
        Step(f"{CARD}[field3]", "Why trading?", "textarea", ["Because markets are a hard ML problem."], "llm"),
    ])


def ashby_plan(cv) -> Plan:
    return Plan([
        Step("_systemfield_name", "Name", "text", ["Ivan Petrov"], "profile"),
        Step("_systemfield_email", "Email", "text", ["d@example.com"], "profile"),
        Step("_systemfield_resume", "Resume", "file", [str(cv)], "resume"),
        Step("p_phone", "Phone Number", "text", ["+7 900 123 4567"], "profile"),
        Step("_systemfield_location", "Current Location", "location", ["Moscow, Moscow City, Russia"], "profile"),
        Step("q_bool", "Authorized?", "select", ["No"], "llm"),
        Step("q_radio", "Gender", "select", ["Prefer not to say"], "answers"),
        Step("q_multi", "Languages", "multiselect", ["Python", "C++"], "llm"),
        Step("q_long", "Why us?", "textarea", ["Hard problems."], "llm"),
    ])


def test_place_match():
    assert place_match("Moscow, RUS", "Moscow, Moscow City, Russia")
    assert place_match("Moscow, Russia", "Moscow, Moscow City, Russia")
    assert place_match("Moscow, Moscow City, Russia", "Moscow, Moscow City, Russia")
    assert not place_match("Moscow, ID, USA", "Moscow, Moscow City, Russia")
    assert not place_match("Moscow Oblast, Russia", "Moscow, Moscow City, Russia")
    assert not place_match("Moscow", "Moscow, Moscow City, Russia")


def test_lever_fill_verify_submit(page, fast, tmp_path):
    cfg, cv = fast
    ap = LeverApplier(page, cfg, "auto")
    ap.open(LEVER_URL)
    ap.fill(lever_plan(cv), tmp_path)
    assert ap.verify(lever_plan(cv)) == []
    assert page.locator("#location-input").input_value() == "Moscow, RUS"  # не «Moscow, ID, USA»
    assert page.locator(f'input[name="{CARD}[field1]"][value="Singapore"]').is_checked() is False
    assert page.locator("#resume-upload-input").evaluate("e => e.files[0].name") == "cv.pdf"
    assert ap.submit("Wintermute — Trader") == ("applied", "отправлено автоматически")


def test_lever_unknown_option(page, fast, tmp_path):
    cfg, cv = fast
    ap = LeverApplier(page, cfg, "dry_run")
    ap.open(LEVER_URL)
    with pytest.raises(FormError, match="нет варианта"):
        ap.fill(lever_plan(cv, level="Expert"), tmp_path)


def test_ashby_fill_verify_submit(page, fast, tmp_path):
    cfg, cv = fast
    ap = AshbyApplier(page, cfg, "auto")
    ap.open(ASHBY_URL)
    ap.fill(ashby_plan(cv), tmp_path)
    assert ap.verify(ashby_plan(cv)) == []
    assert page.locator('[data-field-path="_systemfield_location"] input').input_value() == "Moscow, Russia"
    assert page.locator('button[data-option="no"]').get_attribute("aria-pressed") == "true"
    assert page.locator("#m2").is_checked() is False and page.locator("#r1").is_checked()
    assert ap.submit("Wincent — QR") == ("applied", "отправлено автоматически")


def test_ashby_verify_catches_wrong_answer(page, fast, tmp_path):
    cfg, cv = fast
    ap = AshbyApplier(page, cfg, "dry_run")
    ap.open(ASHBY_URL)
    ap.fill(ashby_plan(cv), tmp_path)
    page.locator('button[data-option="yes"]').click()  # кто-то поменял ответ после заполнения
    assert any("Authorized?" in p for p in ap.verify(ashby_plan(cv)))


def test_apply_one_routes_by_ats(page, fast, db, monkeypatch):
    cfg, cv = fast
    monkeypatch.setattr(autopilot.ashby, "form_url", lambda t, e: ASHBY_URL)
    db.seen("ashby:wincent:1", firm="Wincent", ats="ashby", token="wincent", ext_id="1", title="QR")
    db.update("ashby:wincent:1", status="scored", score=90)
    assert autopilot.apply_one(cfg, db, page, db.get("ashby:wincent:1"), ashby_plan(cv), "auto") == "applied"
    assert db.get("ashby:wincent:1")["status"] == "applied"
