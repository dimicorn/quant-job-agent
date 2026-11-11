"""План заполнения формы: профиль, банк ответов, согласия, LLM (подставной) и вопросы для человека."""
from quant_agent import answers
from quant_agent.config import Education
from quant_agent.sources.greenhouse import Field, parse_questions

from .conftest import fixture

ROW = {"uid": "greenhouse:optiverus:1", "firm": "Optiver", "title": "Graduate QR", "location": "Amsterdam",
       "track": "qr", "letter": "Dear Optiver team, ..."}


def setup_profile(cfg, tmp_path):
    cfg.profile.first_name, cfg.profile.last_name = "Ivan", "Petrov"
    cfg.profile.email, cfg.profile.phone, cfg.profile.linkedin = "d@example.com", "+7 926 000 00 00", "linkedin.com/in/d"
    cfg.profile.city = "Moscow, Moscow City, Russia"
    cfg.profile.education = [Education(school="MIPT", degree="Master's Degree", discipline="Computer Science",
                                       start=(9, 2024), end=(6, 2026))]
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    cfg.resume_pdfs = {"qr": str(pdf)}


def fake_llm(monkeypatch, answers_by_id):
    seen = []

    def fake(cfg, system, user, model, purpose="other", backend=None):
        seen.append((system, user))
        items = [dict(id=i, answer=a, needs_human=not a, why="нет в фактах" if not a else "из резюме")
                 for i, a in answers_by_id.items() if f'"id": "{i}"' in user]
        return model(items=items)
    monkeypatch.setattr(answers, "ask_json", fake)
    return seen


def test_parse_questions_from_real_form():
    fields = parse_questions(fixture("greenhouse_questions.json"))
    ids = [f.id for f in fields]
    assert ids[:5] == ["first_name", "last_name", "email", "phone", "resume"]
    assert "resume_text" not in ids
    data = fixture("greenhouse_questions.json")
    data["location_questions"] = [
        {"label": "Longitude", "required": True, "fields": [{"name": "longitude", "type": "input_hidden"}]},
        {"label": "Location", "required": True, "fields": [{"name": "location", "type": "input_text"}]}]
    loc_ids = [f.id for f in parse_questions(data)]
    assert "location" in loc_ids and "longitude" not in loc_ids
    q = next(f for f in fields if f.id == "question_36875673002")
    assert q.is_select and not q.multi and q.options == ["Yes", "No"] and q.required
    assert next(f for f in fields if f.id.endswith("[]")).multi


def test_plan_sources_and_missing(cfg, tmp_path, monkeypatch):
    setup_profile(cfg, tmp_path)
    answers.save_answer(cfg, "In the past 8 months, have you applied to and completed an assessment?*", "No")
    fields = [
        Field("first_name", "First Name", True, "input_text"),
        Field("email", "Email", True, "input_text"),
        Field("resume", "Resume/CV", True, "input_file"),
        Field("q_linkedin", "LinkedIn Profile", False, "input_text"),
        Field("location", "Location", True, "input_text"),
        Field("q_past", "In the past 8 months, have you applied to and completed an assessment", True,
              "multi_value_single_select", ["Yes", "No"]),
        Field("q_terms", "Terms and Conditions", True, "multi_value_single_select", ["Yes, I agree."]),
        Field("q_office", "Which office location are you interested in?", True, "multi_value_single_select",
              ["Amsterdam", "London", "Both"]),
        Field("q_gpa", "What is your undergraduate academic performance rating?", True, "multi_value_single_select",
              ["90-100%", "80-89%"]),
        Field("q_bad", "Pronouns", True, "multi_value_single_select", ["She/Her", "He/Him"]),
    ]
    seen = fake_llm(monkeypatch, {"q_office": ["Amsterdam"], "q_gpa": [], "q_bad": ["he"]})
    p = answers.plan(cfg, ROW, fields)
    src = {s.id: (s.source, s.value) for s in p.steps}
    assert src["first_name"] == ("profile", ["Ivan"])
    assert src["resume"][0] == "resume" and src["resume"][1][0].endswith("cv.pdf")
    assert src["q_linkedin"] == ("profile", ["linkedin.com/in/d"])
    assert src["location"] == ("profile", ["Moscow, Moscow City, Russia"])
    assert src["q_past"] == ("answers", ["No"])            # из answers.yaml, звёздочка и «?» не мешают
    assert src["q_terms"] == ("consent", ["Yes, I agree."])
    assert src["q_office"] == ("llm", ["Amsterdam"])
    missing = {m["id"]: m["why"] for m in p.missing}
    assert set(missing) == {"q_gpa", "q_bad"}              # LLM не знает / ответ не из списка
    assert "ответ не из списка" in missing["q_bad"]
    # в LLM ушли только вопросы, на которые нет ответа в профиле, банке и согласиях
    user = seen[0][1]
    assert '"q_office"' in user and '"q_past"' not in user and '"first_name"' not in user
    assert "never guess" in seen[0][0]


def test_saved_answer_is_reused_and_plan_roundtrip(cfg, tmp_path, monkeypatch):
    setup_profile(cfg, tmp_path)
    fake_llm(monkeypatch, {})
    f = Field("q1", "Please confirm the region you are currently situated and studying in*", True,
              "multi_value_single_select", ["UK", "Europe", "Middle East"])
    assert answers.plan(cfg, ROW, [f]).missing
    answers.save_answer(cfg, f.label, "Europe")
    p = answers.plan(cfg, ROW, [f])
    assert not p.missing and p.steps[0].value == ["Europe"]
    assert answers.Plan.from_json(p.to_json()).steps[0].value == ["Europe"]
    assert "Please confirm the region" in cfg.path("answers.yaml").read_text(encoding="utf-8")


def test_same_question_other_options_saved_per_firm(cfg, tmp_path, monkeypatch):
    """Настоящий случай: «How did you hear about us?» у Pinely и Marshall Wace с разными вариантами.
    Общий ответ не подходит MW — ответ MW сохраняется только для MW и общий не затирает."""
    setup_profile(cfg, tmp_path)
    fake_llm(monkeypatch, {})
    label = "How did you hear about us?"
    pinely = Field("q1", label, True, "multi_value_single_select", ["Online Job boards", "Friends"])
    mw = Field("q2", label, True, "multi_value_single_select", ["GitHub", "Search engine (Google, Yahoo, etc.)"])
    row_mw = {**ROW, "firm": "Marshall Wace"}
    answers.save_answer(cfg, label, "Online Job boards", firm="Pinely")
    assert answers.plan(cfg, row_mw, [mw]).missing
    answers.save_answer(cfg, label, "Search engine (Google, Yahoo, etc.)", firm="Marshall Wace")
    assert answers.plan(cfg, row_mw, [mw]).steps[0].value == ["Search engine (Google, Yahoo, etc.)"]
    assert answers.plan(cfg, {**ROW, "firm": "Pinely"}, [pinely]).steps[0].value == ["Online Job boards"]
    text = cfg.path("answers.yaml").read_text(encoding="utf-8")
    assert "Marshall Wace: How did you hear about us?" in text and "Online Job boards" in text


def test_consent_needs_setting(cfg, tmp_path, monkeypatch):
    setup_profile(cfg, tmp_path)
    cfg.apply.auto_consent = False
    fake_llm(monkeypatch, {})
    f = Field("q_terms", "Terms and Conditions", True, "multi_value_single_select", ["Yes, I agree."])
    assert [m["id"] for m in answers.plan(cfg, ROW, [f]).missing] == ["q_terms"]


def test_link_fields_only_when_asking_for_link(cfg, tmp_path, monkeypatch):
    """Настоящий случай Mako: вопрос «как узнали о нас (например, LinkedIn)» — не поле для ссылки."""
    setup_profile(cfg, tmp_path)
    cfg.profile.github = "github.com/d"
    fake_llm(monkeypatch, {"q_hear": []})
    fields = [Field("q_li", "LinkedIn Profile", False, "input_text"),
              Field("q_gh", "GitHub URL*", False, "input_text"),
              Field("q_hear", "How did you first hear about Mako? (e.g., LinkedIn, company website, university career "
                    "services, etc.)", True, "input_text")]
    p = answers.plan(cfg, ROW, fields)
    assert {s.id: s.value[0] for s in p.steps} == {"q_li": "linkedin.com/in/d", "q_gh": "github.com/d"}
    assert [m["id"] for m in p.missing] == ["q_hear"]


def test_hh_agent_facts_are_included(cfg, tmp_path):
    from quant_agent.analyst import candidate_facts
    hh = tmp_path / "hh"
    hh.mkdir()
    (hh / "config.yaml").write_text("questions:\n  facts: |\n    Срок выхода: 1–2 недели.\n", encoding="utf-8")
    (hh / "faq.yaml").write_text("- match: [когда готовы выйти]\n  answer: Через 1–2 недели.\n", encoding="utf-8")
    cfg.hh_agent_dir = str(hh)
    facts = candidate_facts(cfg)
    assert "Срок выхода: 1–2 недели." in facts and "когда готовы выйти → Через 1–2 недели." in facts
    assert "не использовать" in facts  # рублёвые зарплаты — не для зарубежных вакансий
    cfg.hh_agent_dir = str(tmp_path / "нет")
    assert "hh-agent" not in candidate_facts(cfg)


def test_plan_lever_standard_fields(cfg, tmp_path, monkeypatch):
    """Lever/Ashby: стандартные поля определяются по Field.std, а не по id (name, _systemfield_name…)."""
    from quant_agent.sources import lever

    from .conftest import FIXTURES
    setup_profile(cfg, tmp_path)
    fake_llm(monkeypatch, {})
    fields = lever.parse_form((FIXTURES / "lever_form.html").read_text(encoding="utf-8"))
    p = answers.plan(cfg, ROW, fields)
    src = {s.id: (s.source, s.kind, s.value) for s in p.steps}
    assert src["name"] == ("profile", "text", ["Ivan Petrov"])
    assert src["location"] == ("profile", "location", ["Moscow, Moscow City, Russia"])
    assert src["resume"][0] == "resume" and src["urls[LinkedIn]"][2] == ["linkedin.com/in/d"]
    assert all(m["required"] for m in p.missing)   # необязательные без ответа — не вопросы для человека
