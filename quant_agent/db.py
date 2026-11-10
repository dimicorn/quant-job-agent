"""SQLite (WAL, соединение на поток). Запись — только колонок из COLUMNS.

Статусы вакансии:
  new → filtered (локальный фильтр) | ineligible (условия допуска или виза) | scored (оценил LLM)
  scored → needs_answers (в анкете вопросы для вас) → scored
  scored → skipped | manual | error | applied (→ oa → interview → offer | rejected)
  closed — вакансия пропала с доски фирмы до того, как вы подали заявку."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    uid             TEXT PRIMARY KEY,      -- ats:token:id
    firm            TEXT,
    ats             TEXT,
    token           TEXT,
    ext_id          TEXT,
    title           TEXT,
    location        TEXT,
    kind            TEXT,                  -- internship | graduate | full_time
    url             TEXT,
    apply_url       TEXT,
    department      TEXT,
    description     TEXT,
    compensation    TEXT,
    published_at    TEXT,
    first_seen      TEXT,
    last_seen       TEXT,
    closed_at       TEXT,
    status          TEXT DEFAULT 'new',
    filter_reason   TEXT,
    score           INTEGER,
    track           TEXT,                  -- qr | qd | infra | other
    confidence      TEXT,                  -- JSON {измерение: supported | partial | unknown}
    checks          TEXT,                  -- JSON: что проверить, чтобы решение поменялось
    reasons         TEXT,                  -- JSON
    risks           TEXT,                  -- JSON
    ats_score       INTEGER,
    ats_have        TEXT,                  -- JSON
    ats_missing     TEXT,                  -- JSON
    letter          TEXT,                  -- сопроводительное письмо
    letter_issues   TEXT,                  -- JSON: что в письме не подтверждается резюме
    letter_at       TEXT,
    plan            TEXT,                  -- JSON: план заполнения формы (answers.Plan)
    error           TEXT,
    notified_at     TEXT,
    decided_at      TEXT,
    applied_at      TEXT,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS ix_jobs_firm ON jobs(firm);

-- Каждый опрос доски: ok=0 — ошибка (не путать с «0 вакансий»)
CREATE TABLE IF NOT EXISTS source_health (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT,
    firm   TEXT,
    ats    TEXT,
    ok     INTEGER,
    count  INTEGER,
    error  TEXT
);
CREATE INDEX IF NOT EXISTS ix_health_firm ON source_health(firm, ts);
"""

COLUMNS = {
    "firm", "ats", "token", "ext_id", "title", "location", "kind", "url", "apply_url", "department",
    "description", "compensation", "published_at", "first_seen", "last_seen", "closed_at", "status",
    "filter_reason", "score", "track", "confidence", "checks", "reasons", "risks", "ats_score", "ats_have",
    "ats_missing", "notified_at", "decided_at", "applied_at", "note", "letter", "letter_issues", "letter_at", "plan", "error",
}
JSON_COLUMNS = {"confidence", "checks", "reasons", "risks", "ats_have", "ats_missing", "letter_issues"}

OPEN = ("new", "filtered", "ineligible", "scored", "skipped", "needs_answers", "manual", "error")  # закрытие с доски меняет статус только у них
APPLIED = ("applied", "oa", "interview", "offer", "rejected")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _enc(k: str, v: Any) -> Any:
    return json.dumps(v, ensure_ascii=False) if k in JSON_COLUMNS and not isinstance(v, str) and v is not None else v


def decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for k in JSON_COLUMNS:
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                pass
    return d


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        for col in ("letter", "letter_issues", "letter_at", "plan", "error"):  # база создана до этих колонок
            if col not in cols:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
        self.conn.commit()

    # ── вакансии ──
    def get(self, uid: str) -> dict[str, Any] | None:
        return decode(self.conn.execute("SELECT * FROM jobs WHERE uid=?", (uid,)).fetchone())

    def seen(self, uid: str, **fields: Any) -> bool:
        """Вакансия есть на доске: новая — вставить (True), известная — обновить last_seen (и снять closed)."""
        t = now()
        cur = self.conn.execute("SELECT status, closed_at FROM jobs WHERE uid=?", (uid,)).fetchone()
        if cur is None:
            fields = {k: _enc(k, v) for k, v in fields.items() if k in COLUMNS}
            fields.update(first_seen=t, last_seen=t)
            cols = ", ".join(["uid", *fields])
            self.conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({', '.join('?' * (len(fields) + 1))})",
                              (uid, *fields.values()))
            self.conn.commit()
            return True
        if cur["status"] == "closed":  # вернулась на доску
            self.conn.execute("UPDATE jobs SET status=COALESCE(NULLIF(note, ''), 'new'), closed_at=NULL, note=NULL, "
                              "last_seen=? WHERE uid=?", (t, uid))
        else:
            self.conn.execute("UPDATE jobs SET last_seen=?, closed_at=NULL WHERE uid=?", (t, uid))
        self.conn.commit()
        return False

    def update(self, uid: str, **fields: Any) -> None:
        bad = set(fields) - COLUMNS
        if bad:
            raise KeyError(bad)
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {sets} WHERE uid=?", (*(_enc(k, v) for k, v in fields.items()), uid))
        self.conn.commit()

    def close_missing(self, firm: str, ats: str, token: str, present: set[str]) -> int:
        """Вакансий фирмы нет в свежем ответе доски — закрыты. Прежний статус — в note, чтобы вернуть, если
        вакансия появится снова. У поданных заявок статус не меняется, только closed_at."""
        rows = self.conn.execute("SELECT uid, status FROM jobs WHERE firm=? AND ats=? AND token=? AND closed_at IS NULL",
                                 (firm, ats, token)).fetchall()
        t, n = now(), 0
        for r in rows:
            if r["uid"] in present:
                continue
            if r["status"] in OPEN:
                self.conn.execute("UPDATE jobs SET status='closed', note=?, closed_at=? WHERE uid=?", (r["status"], t, r["uid"]))
            else:
                self.conn.execute("UPDATE jobs SET closed_at=? WHERE uid=?", (t, r["uid"]))
            n += 1
        self.conn.commit()
        return n

    def by_status(self, *statuses: str, order: str = "first_seen") -> list[dict[str, Any]]:
        q = f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY {order}"
        return [decode(r) for r in self.conn.execute(q, statuses)]

    def for_review(self, min_score: int, tiers: dict[str, int]) -> list[dict[str, Any]]:
        rows = [decode(r) for r in self.conn.execute(
            "SELECT * FROM jobs WHERE status='scored' AND score>=? AND closed_at IS NULL", (min_score,))]
        return sorted(rows, key=lambda r: (tiers.get(r["firm"], 9), -(r["score"] or 0), r["first_seen"]))

    def to_notify(self, min_score: int) -> list[dict[str, Any]]:
        return [decode(r) for r in self.conn.execute(
            "SELECT * FROM jobs WHERE status='scored' AND score>=? AND notified_at IS NULL AND closed_at IS NULL "
            "ORDER BY score DESC", (min_score,))]

    def applications(self, firm: str, months: int) -> int:
        since = (datetime.now() - timedelta(days=30 * months)).isoformat(timespec="seconds")
        q = f"SELECT COUNT(*) FROM jobs WHERE firm=? AND status IN ({','.join('?' * len(APPLIED))}) AND applied_at>=?"
        return self.conn.execute(q, (firm, *APPLIED, since)).fetchone()[0]

    def applied_today(self) -> int:
        today = datetime.now().date().isoformat()
        return self.conn.execute("SELECT COUNT(*) FROM jobs WHERE applied_at >= ?", (today,)).fetchone()[0]

    def counts(self) -> dict[str, int]:
        return {r[0]: r[1] for r in self.conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")}

    # ── здоровье источников ──
    def log_health(self, firm: str, ats: str, ok: bool, count: int = 0, error: str = "") -> None:
        self.conn.execute("INSERT INTO source_health (ts, firm, ats, ok, count, error) VALUES (?,?,?,?,?,?)",
                          (now(), firm, ats, int(ok), count, error))
        self.conn.commit()

    def health(self) -> list[dict[str, Any]]:
        """Последний опрос каждой фирмы и сколько ошибок подряд перед ним."""
        out = []
        for (firm,) in self.conn.execute("SELECT DISTINCT firm FROM source_health ORDER BY firm"):
            rows = self.conn.execute("SELECT * FROM source_health WHERE firm=? ORDER BY id DESC LIMIT 20", (firm,)).fetchall()
            fails = 0
            for r in rows:
                if r["ok"]:
                    break
                fails += 1
            out.append({**dict(rows[0]), "fails_in_row": fails})
        return out
