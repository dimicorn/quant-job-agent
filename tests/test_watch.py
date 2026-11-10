"""Цикл watch на подставных досках и подставном LLM: новые вакансии, фильтры, допуск, оценка, закрытие,
здоровье источников. Сети и настоящего LLM нет."""
from quant_agent import analyst, watch
from quant_agent.firms import Firm, load_firms
from quant_agent.jobs import Job
from quant_agent.sources import SourceError, greenhouse

from .conftest import fixture

FIRM = Firm(name="Optiver", ats="greenhouse", token="optiverus", tier=1)


def board(monkeypatch, jobs_by_token):
    """Подменить fetch Greenhouse: token → список вакансий или исключение."""
    def fake(firm, token):
        v = jobs_by_token[token]
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(greenhouse, "fetch", fake)


def fake_llm(monkeypatch, score=85, blocker=""):
    seen = []

    def fake(cfg, system, user, model, purpose="other", backend=None):
        uids = [line.split('"')[1] for line in user.splitlines() if line.startswith("<job uid=")]
        seen.append((system, user))
        conf = dict(skills="supported", track="supported", eligibility="partial", comp="unknown", red_flags="supported")
        return model(items=[dict(uid=u, score=score, track="qd", confidence=conf, checks=["формат OA"],
                                 reasons=["C++ и Python"], risks=["нужна виза"], blocker=blocker) for u in uids])
    monkeypatch.setattr(analyst, "ask_json", fake)
    return seen


def test_firms_yaml_loads(cfg):
    firms = load_firms(cfg)
    assert len(firms) >= 20 and all(f.max_applications >= 1 for f in firms)
    assert {f.ats for f in firms} <= {"greenhouse", "lever", "ashby", "site"}
    assert all(f.token.startswith("https://") for f in firms if f.ats == "site")


def test_watch_ingests_filters_scores_and_closes(cfg, db, monkeypatch):
    jobs = greenhouse.parse(fixture("greenhouse_jobs.json"), "Optiver", "optiverus")
    board(monkeypatch, {"optiverus": jobs})
    seen = fake_llm(monkeypatch)
    s = watch.run_once(cfg, db, firms=[FIRM])
    # 4 вакансии: стажировка и выпускники в Амстердаме → к оценке; штат — filtered (kinds); Шанхай — filtered (локация)
    assert (s.new, s.filtered, s.scored) == (2, 2, 2)
    scored = db.by_status("scored")
    assert {r["kind"] for r in scored} == {"internship", "graduate"}
    r = scored[0]
    assert r["score"] == 85 and r["confidence"]["skills"] == "supported" and r["checks"] == ["формат OA"]
    assert r["ats_score"] is not None
    # данные вакансий — внутри <job>, правило про инструкции в них — в системном промпте
    system, user = seen[0]
    assert "игнорируй" in system and user.count("<job uid=") == 2

    # повторный опрос: ничего нового, LLM не зовём
    s2 = watch.run_once(cfg, db, firms=[FIRM])
    assert (s2.new, s2.scored, s2.closed) == (0, 0, 0) and len(seen) == 1

    # стажировка пропала с доски — closed; вернулась — прежний статус
    board(monkeypatch, {"optiverus": jobs[1:]})
    assert watch.run_once(cfg, db, firms=[FIRM]).closed == 1
    assert db.get(jobs[0].uid)["status"] == "closed"
    board(monkeypatch, {"optiverus": jobs})
    watch.run_once(cfg, db, firms=[FIRM])
    assert db.get(jobs[0].uid)["status"] == "scored"


def test_board_error_is_logged_and_closes_nothing(cfg, db, monkeypatch):
    jobs = greenhouse.parse(fixture("greenhouse_jobs.json"), "Optiver", "optiverus")
    board(monkeypatch, {"optiverus": jobs})
    fake_llm(monkeypatch)
    watch.run_once(cfg, db, firms=[FIRM])
    board(monkeypatch, {"optiverus": SourceError("HTTP 503")})
    s = watch.run_once(cfg, db, firms=[FIRM])
    assert (s.errors, s.closed) == (1, 0)
    h = db.health()[0]
    assert h["ok"] == 0 and h["fails_in_row"] == 1 and "503" in h["error"]
    assert not db.by_status("closed")


def test_ineligible_without_llm_and_llm_blocker(cfg, db, monkeypatch):
    intern = Job(firm="Optiver", ats="greenhouse", token="optiverus", ext_id="1", title="Quant Research Intern",
                 location="London", description="You are currently pursuing a degree in mathematics.")
    grad = Job(firm="Optiver", ats="greenhouse", token="optiverus", ext_id="2", title="Graduate Quant Developer",
               location="London", description="C++ and Python.")
    board(monkeypatch, {"optiverus": [intern, grad]})
    fake_llm(monkeypatch, blocker="нужен выпуск 2027")
    s = watch.run_once(cfg, db, firms=[FIRM])
    assert s.ineligible == 1 and "студентов" in db.get(intern.uid)["filter_reason"]
    row = db.get(grad.uid)
    assert row["status"] == "ineligible" and row["filter_reason"].startswith("LLM:")


def test_firm_application_limit(cfg, db):
    for i in range(2):
        db.seen(f"greenhouse:optiverus:{i}", firm="Optiver", ats="greenhouse", token="optiverus", title="x")
        db.update(f"greenhouse:optiverus:{i}", status="applied", applied_at=watch.now())
    assert db.applications("Optiver", 6) == 2
    assert db.applications("IMC", 6) == 0


def test_analyze_only_given_uids(cfg, db, monkeypatch):
    """add <url> оценивает одну вакансию, а не все новые в базе."""
    for i in range(3):
        db.seen(f"greenhouse:imc:{i}", firm="IMC", ats="greenhouse", token="imc", title="Graduate", kind="graduate")
    fake_llm(monkeypatch)
    assert analyst.analyze_new(cfg, db, uids={"greenhouse:imc:1"}) == 1
    assert [r["uid"] for r in db.by_status("scored")] == ["greenhouse:imc:1"]


def test_many_matches_give_one_summary_notification(cfg, db, monkeypatch):
    cfg.watch.notify = True
    sent = []
    monkeypatch.setattr(watch, "notify", lambda title, text: sent.append(title))
    jobs = [Job(firm="Optiver", ats="greenhouse", token="optiverus", ext_id=str(i), title=f"Graduate Researcher {i}",
                location="London") for i in range(5)]
    board(monkeypatch, {"optiverus": jobs})
    fake_llm(monkeypatch)
    assert watch.run_once(cfg, db, firms=[FIRM]).notified == 5
    assert sent == ["Новых подходящих вакансий: 5"]
    sent.clear()
    board(monkeypatch, {"optiverus": jobs[:2] + [Job(firm="Optiver", ats="greenhouse", token="optiverus", ext_id="9",
                                                     title="Graduate Developer", location="London")]})
    watch.run_once(cfg, db, firms=[FIRM])
    assert sent == ["Optiver: 85"]  # одна новая — отдельное уведомление


def test_watch_aggregator_adds_new_and_skips_known(cfg, db, monkeypatch):
    """Списки SimplifyJobs: новая вакансия дочитывается и попадает в базу, уже известная — нет."""
    from quant_agent import watch
    from quant_agent.firms import Firm
    from quant_agent.jobs import Job
    firms = [Firm(name="Jump Trading", ats="greenhouse", token="jumptrading")]
    cfg.watch.aggregators = ["https://example.org/SimplifyJobs/Summer2027-Internships/listings.json"]
    known = Job(firm="Jump Trading", ats="greenhouse", token="jumptrading", ext_id="1", title="QR Intern",
                location="London, UK", hints=["Internship"])
    new = Job(firm="SIG", ats="page", token="careers-sig.icims.com", ext_id="b2", title="Quant Research Intern",
              location="London, UK", url="https://careers-sig.icims.com/jobs/1", hints=["Internship"])
    db.seen(known.uid, firm=known.firm, ats="greenhouse", token="jumptrading", ext_id="1", title=known.title)
    monkeypatch.setattr(watch, "fetch_all", lambda firms, workers: [])
    monkeypatch.setattr(watch.simplify, "fetch", lambda url, firms: [known, new])
    monkeypatch.setattr(watch, "get", lambda url: b"<title>SIG</title><p>Probability, C++</p>")
    s = watch.run_once(cfg, db, firms, score=False)
    assert s.new == 1
    row = db.get(new.uid)
    assert row["status"] == "new" and "Probability" in row["description"] and row["firm"] == "SIG"
