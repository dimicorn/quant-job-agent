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

    class NoBrowser:
        def page(self):
            return None
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
