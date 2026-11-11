"""Автопилот без браузера и сети: выбор вакансий, лимиты, вопросы для человека."""
from datetime import datetime

from quant_agent import autopilot
from quant_agent.answers import Plan, Step
from quant_agent.firms import Firm

FIRMS = [Firm(name="Optiver", ats="greenhouse", token="optiverus", tier=1, max_applications=1),
         Firm(name="IMC", ats="greenhouse", token="imc", tier=1), Firm(name="Voleon", ats="ashby", token="voleon", tier=1)]


def add(db, uid, firm, score, ats="greenhouse", letter="Dear team", status="scored"):
    token = uid.split(":")[1]
    db.seen(uid, firm=firm, ats=ats, token=token, ext_id=uid.split(":")[2], title=f"{firm} role {score}", location="London")
    db.update(uid, status=status, score=score, letter=letter)


def test_candidates_one_per_firm_and_limits(cfg, db):
    cfg.apply.min_score = 80
    add(db, "greenhouse:optiverus:1", "Optiver", 90)
    add(db, "greenhouse:optiverus:2", "Optiver", 95)
    add(db, "greenhouse:imc:3", "IMC", 85, letter=None)       # нет письма
    add(db, "greenhouse:imc:4", "IMC", 82)
    add(db, "greenhouse:imc:5", "IMC", 70)                    # ниже порога
    add(db, "ashby:voleon:6", "Voleon", 99, ats="ashby")      # Ashby тоже заполняется
    got = [r["uid"] for r in autopilot.candidates(cfg, db, FIRMS)]
    assert got == ["ashby:voleon:6", "greenhouse:optiverus:2", "greenhouse:imc:4"]  # по tier, потом по баллу
    # в Optiver лимит 1 заявка — после неё фирма пропадает из кандидатов
    db.update("greenhouse:optiverus:2", status="applied", applied_at=autopilot.now())
    assert [r["uid"] for r in autopilot.candidates(cfg, db, FIRMS)] == ["ashby:voleon:6", "greenhouse:imc:4"]


def test_run_once_needs_answers_and_daily_limit(cfg, db, monkeypatch):
    cfg.apply.min_score, cfg.apply.daily_limit = 80, 1
    cfg.apply.delay_seconds, cfg.apply.active_hours = (0, 0), (0, 24)
    add(db, "greenhouse:optiverus:1", "Optiver", 95)
    add(db, "greenhouse:imc:2", "IMC", 90)
    monkeypatch.setattr(autopilot, "load_firms", lambda cfg: FIRMS)
    monkeypatch.setattr(autopilot.greenhouse, "questions", lambda t, e: [])
    plans = {"greenhouse:optiverus:1": Plan([], [dict(id="q", label="GPA?", options=["A"], required=True, why="нет")]),
             "greenhouse:imc:2": Plan([Step("first_name", "First", "text", ["D"], "profile")], [])}
    monkeypatch.setattr(autopilot.answers, "plan", lambda cfg, row, fields: plans[row["uid"]])
    applied = []

    def fake_apply(cfg, db, page, row, plan, mode=None):
        applied.append(row["uid"])
        db.update(row["uid"], status="applied", applied_at=autopilot.now())
        return "applied"
    monkeypatch.setattr(autopilot, "apply_one", fake_apply)

    assert autopilot.run_once(cfg, db, NoBrowser(), poll=False) == 1
    assert applied == ["greenhouse:imc:2"]
    row = db.get("greenhouse:optiverus:1")
    assert row["status"] == "needs_answers" and "GPA?" in row["error"]
    # дневной лимит 1 исчерпан — следующий проход ничего не подаёт
    add(db, "greenhouse:imc:9", "IMC", 99)
    assert autopilot.run_once(cfg, db, NoBrowser(), poll=False) == 0


def test_active_hours(cfg):
    cfg.apply.active_hours, cfg.apply.weekdays_only = (9, 18), True
    assert autopilot.in_hours(cfg, datetime(2026, 11, 9, 10))       # понедельник
    assert not autopilot.in_hours(cfg, datetime(2026, 11, 9, 20))
    assert not autopilot.in_hours(cfg, datetime(2026, 11, 8, 10))   # воскресенье


class NoBrowser:
    def page(self):
        return None

    def close(self):
        pass


def _fake_apply(applied):
    def fake(cfg, db, page, row, plan, mode=None):
        applied.append(row["uid"])
        db.update(row["uid"], status="applied", applied_at=autopilot.now())
        return "applied"
    return fake


def test_run_once_drains_queue_and_spreads_firms(cfg, db, monkeypatch):
    """Вторая вакансия фирмы уходит в том же проходе (лимит 2), но после первых вакансий других фирм."""
    cfg.apply.min_score, cfg.apply.daily_limit = 80, 10
    cfg.apply.delay_seconds, cfg.apply.active_hours = (0, 0), (0, 24)
    firms = [Firm(name="IMC", ats="greenhouse", token="imc", tier=1, max_applications=2),
             Firm(name="Optiver", ats="greenhouse", token="optiverus", tier=1, max_applications=2)]
    add(db, "greenhouse:imc:1", "IMC", 95)
    add(db, "greenhouse:imc:2", "IMC", 94)
    add(db, "greenhouse:imc:3", "IMC", 93)                  # третья — сверх лимита фирмы
    add(db, "greenhouse:optiverus:4", "Optiver", 85)
    monkeypatch.setattr(autopilot, "load_firms", lambda cfg: firms)
    monkeypatch.setattr(autopilot, "prepare", lambda cfg, db, row: Plan([], []))
    applied = []
    monkeypatch.setattr(autopilot, "apply_one", _fake_apply(applied))
    assert autopilot.run_once(cfg, db, NoBrowser(), poll=False) == 3
    assert applied == ["greenhouse:imc:1", "greenhouse:optiverus:4", "greenhouse:imc:2"]


def test_run_once_applies_what_background_poll_finds(cfg, db, monkeypatch):
    """Пока опрос и оценка идут в фоне, заявки ждут; новая подходящая вакансия подаётся в том же проходе."""
    cfg.apply.min_score, cfg.apply.daily_limit = 80, 10
    cfg.apply.delay_seconds, cfg.apply.active_hours = (0, 0), (0, 24)
    monkeypatch.setattr(autopilot, "load_firms", lambda cfg: FIRMS)
    monkeypatch.setattr(autopilot, "prepare", lambda cfg, db, row: Plan([], []))
    monkeypatch.setattr(autopilot.stop, "wait", lambda s: __import__("time").sleep(0.05) or False)

    def fake_poll(cfg, pdb, firms):
        __import__("time").sleep(0.2)
        add(pdb, "greenhouse:imc:7", "IMC", 91)
    monkeypatch.setattr(autopilot.watch, "run_once", fake_poll)
    monkeypatch.setattr(autopilot.materials, "write_letters", lambda *a, **k: None)
    applied = []
    monkeypatch.setattr(autopilot, "apply_one", _fake_apply(applied))
    assert autopilot.run_once(cfg, db, NoBrowser()) == 1
    assert applied == ["greenhouse:imc:7"]


def test_run_once_no_race_when_background_finishes_during_queue_check(cfg, db, monkeypatch):
    """Фон дописал вакансию и закончил, пока считалась пустая очередь: проход не должен выйти, не подав её."""
    cfg.apply.min_score, cfg.apply.daily_limit = 80, 10
    cfg.apply.delay_seconds, cfg.apply.active_hours = (0, 0), (0, 24)
    monkeypatch.setattr(autopilot, "load_firms", lambda cfg: FIRMS)
    monkeypatch.setattr(autopilot, "prepare", lambda cfg, db, row: Plan([], []))
    monkeypatch.setattr(autopilot.stop, "wait", lambda s: False)
    monkeypatch.setattr(autopilot.watch, "run_once", lambda cfg, pdb, firms: add(pdb, "greenhouse:imc:8", "IMC", 90))
    monkeypatch.setattr(autopilot.materials, "write_letters", lambda *a, **k: None)
    real, calls = autopilot.candidates, []

    def slow_first(*a, **k):
        calls.append(1)
        if len(calls) == 1:  # очередь посчитана до записи фона, а фон закончил, пока мы её возвращали
            out = [r for r in real(*a, **k) if r["uid"] != "greenhouse:imc:8"]
            while any(t.name == "опрос-оценка" for t in __import__("threading").enumerate()):
                __import__("time").sleep(0.01)
            return out
        return real(*a, **k)
    monkeypatch.setattr(autopilot, "candidates", slow_first)
    applied = []
    monkeypatch.setattr(autopilot, "apply_one", _fake_apply(applied))
    assert autopilot.run_once(cfg, db, NoBrowser()) == 1
    assert applied == ["greenhouse:imc:8"]
