"""Коды подтверждения из почты: разбор писем, выбор свежего кода, ввод кода в форму после Submit."""
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime

import pytest

from quant_agent import applier, mail
from quant_agent.applier import Applier

GH_BODY = ("Hi Ivan,\n\nCopy and paste this code into the security code field on your application: dU9xKmLP\n\n"
           "After you enter the code, resubmit your application.\n")


def raw(subject: str, body: str, when: datetime, html: bool = False) -> bytes:
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = "no-reply@us.greenhouse-mail.io", subject, format_datetime(when)
    if html:
        m.set_content("<p>" + body.replace("\n", "<br>") + "</p>", subtype="html")
    else:
        m.set_content(body)
    return m.as_bytes()


def test_extract_code():
    assert mail.extract_code("Security code for your application to Jane Street", GH_BODY) == "dU9xKmLP"
    assert mail.extract_code("Verify", "Your verification code is 483920.") == "483920"
    assert mail.extract_code("Your code", "Hello,\n\n  K7Q2ZP  \n\nThanks") == "K7Q2ZP"
    # слова после двоеточия — не код
    assert mail.extract_code("Application received", "Use the code below: Thanks for applying") is None
    assert mail.extract_code("Thank you for applying", "Thank you for applying to Optiver: we will be in touch") is None


def test_parse_message_html():
    now = datetime.now(UTC).replace(microsecond=0)
    x = mail.parse_message(raw("Security code for your application to Mako", GH_BODY, now, html=True))
    assert x.subject.startswith("Security code") and x.date == now
    assert mail.extract_code(x.subject, x.body) == "dU9xKmLP"


def test_parse_message_broken_charset():
    # тема объявлена как gb2312, а байты — из gb18030 (так пишут китайские рассылки)
    subj = "=?gb2312?B?" + __import__("base64").b64encode("测试 € 邮件".encode("gb18030")).decode() + "?="
    data = (f"From: x@example.cn\r\nSubject: {subj}\r\nDate: Wed, 11 Nov 2026 10:00:00 +0300\r\n"
            "Content-Type: text/plain; charset=gb2312\r\n\r\n").encode() + "正文 €".encode("gb18030")
    x = mail.parse_message(data)
    assert "邮件" in x.subject and "正文" in x.body


def test_pick_code_fresh_and_firm_first():
    now = datetime.now(UTC)
    old = mail.Letter(now - timedelta(hours=1), "gh", "Security code for your application to Mako",
                      GH_BODY.replace("dU9xKmLP", "OLD1code"))
    other = mail.Letter(now, "gh", "Security code for your application to Optiver", GH_BODY.replace("dU9xKmLP", "Opt1veRx"))
    mine = mail.Letter(now - timedelta(seconds=5), "gh", "Security code for your application to Jane Street", GH_BODY)
    since = now - timedelta(minutes=2)
    assert mail.pick_code([old], since) is None  # письмо старше Submit не берём
    assert mail.pick_code([old, other, mine], since, "Jane Street") == "dU9xKmLP"
    assert mail.pick_code([old, other, mine], since) == "Opt1veRx"  # без фирмы — самое свежее


class FakeIMAP:
    def __init__(self, messages):
        self.messages, self.calls = messages, 0

    def select(self, folder, readonly=False):
        return "OK", [b"1"]

    def search(self, charset, query):
        self.calls += 1
        n = len(self.messages) if self.calls > 1 else 0  # письмо приходит не сразу
        return "OK", [" ".join(str(i + 1) for i in range(n)).encode()]

    def fetch(self, num, what):
        assert "PEEK" in what  # письма остаются непрочитанными
        self.fetched = getattr(self, "fetched", 0) + 1
        return "OK", [(b"1 (BODY[] {1})", self.messages[int(num) - 1]), b")"]

    def noop(self):
        return "OK", []

    def logout(self):
        return "BYE", []


def test_wait_code(cfg, monkeypatch):
    monkeypatch.setattr(mail.stop, "wait", lambda s: False)
    box = FakeIMAP([raw("Security code for your application to Jane Street", GH_BODY, datetime.now(UTC))])
    monkeypatch.setattr(mail, "connect", lambda c: box)
    assert mail.wait_code(cfg, datetime.now(UTC), "Jane Street", timeout=30) == "dU9xKmLP"
    assert box.calls == 2


def test_wait_code_fetches_each_letter_once(cfg, monkeypatch):
    """Пока ждём код, старые письма не качаются заново на каждом круге."""
    monkeypatch.setattr(mail.stop, "wait", lambda s: False)
    old = raw("Weekly digest", "nothing here", datetime.now(UTC) - timedelta(hours=1))
    box = FakeIMAP([old, old])
    box.search = lambda charset, query: ("OK", [b"1 2"])
    monkeypatch.setattr(mail, "connect", lambda c: box)
    t = iter([datetime.now(UTC)] * 4 + [datetime.now(UTC) + timedelta(hours=1)] * 10)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(t)
    monkeypatch.setattr(mail, "datetime", Clock)
    assert mail.wait_code(cfg, datetime.now(UTC), timeout=30) is None
    assert box.fetched == 2


def test_configured(cfg, monkeypatch, tmp_path):
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    cfg.profile.email = "d@example.com"
    assert not mail.configured(cfg)
    (cfg.base_dir / ".env").write_text("export IMAP_PASSWORD='abcd efgh ijkl mnop'\n")
    assert mail.configured(cfg)


FORM = """<!doctype html><html><body><form><input id="first_name">
<div id="code" hidden>Enter the 8-character code sent to your email
""" + "".join(f'<input id="security-input-{i}" maxlength="1">' for i in range(8)) + """</div>
<button type="submit">Submit application</button></form>
<script>
document.querySelector('button').addEventListener('click', e => { e.preventDefault();
  const box = document.querySelector('#code');
  if (box.hidden) { box.hidden = false; return; }  // первый Submit — просим код
  const code = [...document.querySelectorAll('[id^=security-input]')].map(i => i.value).join('');
  if (code === 'dU9xKmLP') location.href = '/confirmation'; });
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


def test_submit_enters_code_from_mail(page, cfg, monkeypatch):
    cfg.apply.read_seconds, cfg.apply.action_seconds = (0, 0), (0, 0)
    seen = {}

    def fake_wait(c, since, hint=""):
        seen["since"], seen["hint"] = since, hint
        return "dU9xKmLP"
    monkeypatch.setattr(mail, "configured", lambda c: True)
    monkeypatch.setattr(mail, "wait_code", fake_wait)
    ap = Applier(page, cfg, "auto")
    ap.firm = "Jane Street"
    ap.open("https://job-boards.greenhouse.io/embed/job_app?for=janestreet&token=1")
    assert ap.submit("Jane Street — QR") == ("applied", "отправлено автоматически")
    assert seen["hint"] == "Jane Street" and datetime.now(UTC) - seen["since"] < timedelta(minutes=1)


def test_submit_without_mail_asks_human(page, cfg, monkeypatch):
    cfg.apply.read_seconds, cfg.apply.action_seconds, cfg.apply.submit_wait_minutes = (0, 0), (0, 0), 0
    monkeypatch.setattr(mail, "configured", lambda c: False)
    monkeypatch.setattr(applier, "notify", lambda *a: seen.append(a))
    seen = []
    ap = Applier(page, cfg, "auto")
    ap.open("https://job-boards.greenhouse.io/embed/job_app?for=janestreet&token=1")
    ap.page.locator("button").click()
    ap.page.locator("#security-input-0").wait_for()
    assert not ap._wait_confirmed(1)
    assert seen and "код" in seen[0][0]
