"""Длинный ответ в поле с лимитом символов и закрытое окно браузера во время заявки."""
import pytest

from quant_agent import autopilot
from quant_agent.answers import Plan, Step
from quant_agent.applier import Applier, FormError, fit

LONG = ("I am not currently enrolled at a university. I completed my MSc in Computer Science at MIPT in June 2026. "
        "I am currently studying at the Yandex School of Data Analysis, which is a non-university program.")
FORM = """<!doctype html><html><body><form><input id="first_name">
<input id="question_1" maxlength="120"><input id="question_2" maxlength="20"><textarea id="question_3"></textarea>
<button type="submit">Submit application</button></form></body></html>"""
URL = "https://job-boards.greenhouse.io/embed/job_app?for=mako&token=1"


def test_fit():
    assert fit(LONG, 120) == ("I am not currently enrolled at a university. "
                              "I completed my MSc in Computer Science at MIPT in June 2026.")
    assert fit(LONG, 10) is None
    assert fit("Short answer.", 255) == "Short answer."


@pytest.fixture
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.route("https://job-boards.greenhouse.io/**", lambda r: r.fulfill(content_type="text/html", body=FORM))
        yield pg
        b.close()


@pytest.fixture
def fast(cfg):
    a = cfg.apply
    a.read_seconds, a.action_seconds, a.typing_delay_ms = (0, 0), (0, 0), (0, 0)
    return cfg


def test_long_answer_shortened_to_maxlength(page, fast, tmp_path):
    plan = Plan([Step("question_1", "Which university are you attending?", "text", [LONG], "llm"),
                 Step("question_3", "Subject", "textarea", ["Line one.\nLine two."], "llm")])
    ap = Applier(page, fast, "dry_run")
    ap.open(URL)
    ap.fill(plan, tmp_path)
    assert ap.verify(plan) == []
    assert page.locator("#question_1").input_value() == fit(LONG, 120)
    assert page.locator("#question_3").input_value() == "Line one.\nLine two."  # в textarea переносы остаются


def test_too_long_first_sentence_is_manual(page, fast, tmp_path):
    plan = Plan([Step("question_2", "University", "text", [LONG], "llm")])
    ap = Applier(page, fast, "dry_run")
    ap.open(URL)
    with pytest.raises(FormError, match="длиннее лимита"):
        ap.fill(plan, tmp_path)


def test_closed_browser_keeps_job_in_queue(page, fast, db):
    uid = "greenhouse:mako:1"
    db.seen(uid, firm="Mako", ats="greenhouse", token="mako", ext_id="1", title="Graduate SWE", status="scored")
    row = {**db.get(uid), "score": 86}
    page.close()
    plan = Plan([Step("question_1", "University", "text", ["MIPT"], "llm")])
    assert autopilot.apply_one(fast, db, page, row, plan, "assist") == "closed"
    assert db.get(uid)["status"] == "scored"


ASHBY_404 = "<html><body><h1>Page not found</h1><p>The page you requested was not found</p></body></html>"


def _ashby_job(db):
    uid = "ashby:pinely:1"
    db.seen(uid, firm="Pinely", ats="ashby", token="pinely", ext_id="1", title="Middle QR", status="scored")
    db.update(uid, status="scored")
    return uid, {**db.get(uid), "score": 82}


def test_removed_form_is_gone_and_not_returned_by_poll(page, fast, db, monkeypatch):
    """Настоящий случай Pinely: API Ashby ещё отдаёт вакансию, а страница формы — «Page not found»."""
    page.route("https://jobs.ashbyhq.com/**", lambda r: r.fulfill(status=404, content_type="text/html", body=ASHBY_404))
    monkeypatch.setattr(autopilot.AshbyApplier, "open", lambda self, url: (
        self.page.goto(url), self.page.locator(self.READY).first.wait_for(timeout=500)))
    uid, row = _ashby_job(db)
    assert autopilot.apply_one(fast, db, page, row, Plan([]), "auto") == "gone"
    assert db.get(uid)["status"] == "gone"
    db.seen(uid)  # вакансия всё ещё в API доски — статус не возвращается
    assert db.get(uid)["status"] == "gone"


def test_form_not_loaded_stays_in_queue(page, fast, db, monkeypatch):
    """Сеть мигнула — формы нет, но и «not found» нет: вакансия остаётся в очереди."""
    page.route("https://jobs.ashbyhq.com/**", lambda r: r.fulfill(content_type="text/html", body="<html><body></body></html>"))
    monkeypatch.setattr(autopilot.AshbyApplier, "open", lambda self, url: (
        self.page.goto(url), self.page.locator(self.READY).first.wait_for(timeout=500)))
    uid, row = _ashby_job(db)
    assert autopilot.apply_one(fast, db, page, row, Plan([]), "auto") == "retry"
    assert db.get(uid)["status"] == "scored"
