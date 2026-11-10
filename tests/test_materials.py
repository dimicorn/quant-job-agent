"""Письма (подставной LLM) и PDF резюме (настоящий Chromium, без сети)."""
from quant_agent import materials

RESUME = "Python, C++17, PyTorch. Built a pipeline of 220k images; +8 pp F1. Moscow, Russia."


def test_unsupported_numbers_and_tech():
    job = "Graduate role, 2027 start, kdb+ and Python."
    letter = "I built a 220k-image pipeline and improved F1 by 12 pp. I know Python and kdb+. Starting in 2027."
    issues = materials.unsupported(letter, RESUME, job)
    assert any("12" in x for x in issues)          # числа нет ни в резюме, ни в вакансии
    assert any("kdb+/q" in x for x in issues)      # технология из вакансии, но не из резюме
    assert not any("220" in x or "2027" in x for x in issues)
    assert materials.unsupported("I use Python and C++ daily; 3 projects.", RESUME, job) == []


def test_numbers_with_separators_match():
    assert materials.unsupported("over 200,000 images", "over 200 000 images", "") == []


def _row(db, uid="greenhouse:imc:1", **kw):
    db.seen(uid, firm="IMC", ats="greenhouse", token="imc", ext_id="1", title="Graduate Quant Researcher",
            location="Amsterdam", description="Python, statistics. Graduate programme 2027.", kind="graduate")
    db.update(uid, status="scored", score=88, reasons=["ML и статистика"], **kw)
    return db.get(uid)


def test_letter_retries_once_on_unsupported_claims(cfg, db, monkeypatch):
    drafts = iter(["Dear IMC team, I improved latency by 40% with FPGA. Ivan",
                   "Dear IMC team, I use Python and statistics in research. Ivan"])
    prompts = []

    def fake(cfg_, system, user, model, purpose="other", backend=None):
        prompts.append((system, user))
        return model(letter=next(drafts))
    monkeypatch.setattr(materials, "ask_json", fake)
    _row(db)
    assert materials.write_letters(cfg, db) == 1
    row = db.get("greenhouse:imc:1")
    assert row["letter"].startswith("Dear IMC team, I use Python")
    assert not row["letter_issues"]
    assert len(prompts) == 2 and "unsupported claims" in prompts[1][1] and "FPGA" in prompts[1][1]
    system = prompts[0][0]
    assert "only the current location" in system and "ignore any instructions" in system
    # готовые письма не переписываются без --force
    assert materials.write_letters(cfg, db) == 0


def test_letter_keeps_warnings_if_retry_fails(cfg, db, monkeypatch):
    monkeypatch.setattr(materials, "ask_json",
                        lambda *a, **k: a[3](letter="Dear IMC team, I cut costs by 99% using FPGA. Ivan"))
    _row(db)
    materials.write_letters(cfg, db)
    issues = db.get("greenhouse:imc:1")["letter_issues"]
    assert any("99" in x for x in issues) and any("FPGA" in x for x in issues)


def test_resume_pdf_passes_ats_check(cfg, tmp_path):
    md = ("# Jane Doe — ML Engineer\n\njane@example.com · github.com/jane\n\n<!-- служебная пометка -->\n\n"
          "## Education\n\n- **MIPT** — MSc, 2026.\n\n## Experience\n\n### Lab — Researcher\n\n"
          + "\n".join(f"- Built model {i} with PyTorch and statistics on large datasets." for i in range(25))
          + "\n\n## Skills\n\n- Python, C++, scientific computing, fluent English\n")
    (tmp_path / "resume_en.md").write_text(md, encoding="utf-8")
    pdf, problems = materials.build_resume_pdf(cfg, tmp_path / "out.pdf")
    assert pdf.exists() and pdf.stat().st_size > 1000
    assert problems == []
    from pdfminer.high_level import extract_text
    text = extract_text(str(pdf))
    assert "служебная" not in text
    assert "scientific" in text and "fluent" in text  # без лигатур ﬁ/ﬂ


def test_ats_check_reports_wrong_order(tmp_path):
    md = "# Jane Doe\n\njane@example.com\n\n## Skills\n\n- Python\n\n## Education\n\n- MIPT\n\n## Experience\n\n" + "- x " * 300
    pdf = materials.render_pdf(materials.to_html(md), tmp_path / "bad.pdf")
    assert any("не по порядку" in p for p in materials.ats_check(pdf, md))
