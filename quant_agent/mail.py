"""Коды подтверждения из почты (IMAP). Greenhouse после Submit просит security code из письма
«Security code for your application to <фирма>»: агент ждёт письмо, достаёт код и вписывает его в форму.

Письма читаются через BODY.PEEK — непрочитанными они и остаются. Разбор письма отделён от сети
(parse_message, extract_code) и проверяется тестами."""
from __future__ import annotations

import email
import imaplib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime

from . import stop
from .config import Config
from .jobs import html_to_text

SUBJECT = re.compile(r"security code|verification code|confirmation code|one[- ]time|\bcode\b|код", re.I)
# «…paste this code into the security code field on your application: dU9xKmLP»
AFTER_COLON = re.compile(r"\bcode\b[^:\n]{0,120}:\s*([A-Za-z0-9]{4,12})\b", re.I)
# «Your verification code is 123456»
CODE_IS = re.compile(r"\bcode\s+is\s*:?\s*([A-Za-z0-9]{4,12})\b", re.I)
ALONE = re.compile(r"^\s*([A-Za-z0-9]{4,12})\s*$", re.M)  # код отдельной строкой


class MailError(Exception):
    """Почта не настроена или не отвечает."""


@dataclass
class Letter:
    date: datetime
    sender: str
    subject: str
    body: str


def _looks_like_code(s: str) -> bool:
    """Не слово: есть цифра или смешанный регистр («dU9xKmLP», «483920»)."""
    return any(c.isdigit() for c in s) or (s != s.lower() and s != s.upper() and not s.istitle())


def extract_code(subject: str, body: str) -> str | None:
    for rx in (AFTER_COLON, CODE_IS):
        for m in rx.finditer(body):
            if _looks_like_code(m.group(1)):
                return m.group(1)
    if SUBJECT.search(subject):
        for m in ALONE.finditer(body):
            if _looks_like_code(m.group(1)):
                return m.group(1)
    return None


# кодировки, которые почтовики объявляют, а пишут в более широкой
WIDER = {"gb2312": "gb18030", "gbk": "gb18030", "ks_c_5601-1987": "cp949", "iso-8859-1": "cp1252"}


def _decode(data: bytes, charset: str | None) -> str:
    cs = (charset or "utf-8").lower()
    for c in (WIDER.get(cs, cs), "utf-8"):
        try:
            return data.decode(c)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


def _header(msg: Message, name: str) -> str:
    """Заголовок по кускам: битая кодировка в одном письме не роняет разбор."""
    out = []
    for part, cs in decode_header(str(msg.get(name, "") or "")):
        out.append(_decode(part, cs) if isinstance(part, bytes) else part)
    return "".join(out).strip()


def _body(msg: Message) -> str:
    plain, rich = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html") or part.get_filename():
            continue
        data = part.get_payload(decode=True) or b""
        text = _decode(data, part.get_content_charset())
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not rich:
            rich = html_to_text(text)
    return plain or rich


def parse_message(raw: bytes) -> Letter:
    msg = email.message_from_bytes(raw)
    try:
        date = parsedate_to_datetime(msg.get("Date"))
        date = date if date.tzinfo else date.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        date = datetime.now(UTC)
    return Letter(date, _header(msg, "From"), _header(msg, "Subject"), _body(msg))


def pick_code(letters: list[Letter], since: datetime, hint: str = "") -> str | None:
    """Код из самого свежего письма не старше since; письма, где упомянута фирма (hint), — первыми."""
    fresh = sorted((x for x in letters if x.date >= since), key=lambda x: x.date, reverse=True)
    h = hint.split()[0].lower() if hint.strip() else ""
    for group in ([x for x in fresh if h and h in (x.subject + x.body).lower()], fresh):
        for x in group:
            code = extract_code(x.subject, x.body)
            if code:
                return code
    return None


def configured(cfg: Config) -> bool:
    return bool(cfg.secret(cfg.mail.password_env) and (cfg.mail.user or cfg.profile.email))


def connect(cfg: Config) -> imaplib.IMAP4_SSL:
    user, password = cfg.mail.user or cfg.profile.email, cfg.secret(cfg.mail.password_env)
    if not (user and password):
        raise MailError(f"нет адреса или пароля: profile.email / mail.user и {cfg.mail.password_env} в .env")
    try:
        box = imaplib.IMAP4_SSL(cfg.mail.imap_host, timeout=30)
        box.login(user, password.replace(" ", ""))  # Google показывает пароль приложения группами через пробел
    except (OSError, imaplib.IMAP4.error) as e:
        raise MailError(f"IMAP {cfg.mail.imap_host}: {e}") from e
    return box


def recent(box: imaplib.IMAP4_SSL, folder: str, since: datetime, limit: int = 30,
           cache: dict | None = None) -> list[Letter]:
    """Письма папки с даты since (IMAP ищет по дням — точнее отсекает pick_code), новые первыми.
    cache — уже разобранные письма (папка, номер) → Letter: при ожидании кода не качать их заново."""
    box.select(f'"{folder}"', readonly=True)
    _, data = box.search(None, f'(SINCE "{since.astimezone(UTC):%d-%b-%Y}")')
    out = []
    for num in reversed((data[0] or b"").split()[-limit:]):
        key = (folder, num)
        if cache is not None and key in cache:
            if cache[key] is not None:
                out.append(cache[key])
            continue
        _, parts = box.fetch(num, "(BODY.PEEK[])")
        raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
        letter = None
        if raw:
            try:
                letter = parse_message(raw)
            except Exception:  # noqa: BLE001 — одно странное письмо не мешает искать код в остальных
                pass
        if letter is not None:
            out.append(letter)
        if cache is not None:
            cache[key] = letter
    return out


def wait_code(cfg: Config, since: datetime, hint: str = "", timeout: float | None = None) -> str | None:
    """Ждём письмо с кодом, пришедшее после since. Нет за timeout — None."""
    timeout = cfg.mail.code_wait_seconds if timeout is None else timeout
    since = since - timedelta(seconds=60)  # часы почтового сервера могут отставать
    end = datetime.now(UTC) + timedelta(seconds=timeout)
    box = connect(cfg)
    cache: dict = {}
    try:
        while True:
            letters = [x for f in cfg.mail.folders for x in recent(box, f, since, cache=cache)]
            code = pick_code(letters, since, hint)
            if code or datetime.now(UTC) >= end or stop.wait(5):
                return code
            box.noop()
    finally:
        try:
            box.logout()
        except Exception:  # noqa: BLE001
            pass
