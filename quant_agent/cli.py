"""quant-agent: команды. uv run quant-agent help."""
from __future__ import annotations

import argparse
import subprocess
import sys
import webbrowser

from rich.table import Table

from . import analyst, materials, stop, watch
from .config import Config, load_config
from .db import APPLIED, DB, now
from .firms import load_firms, validate
from .jobs import classify_kind, eligibility
from .log import console
from .sources import SOURCES, SourceError, detect, fetch_url

STATUSES = ("new", "filtered", "ineligible", "scored", "needs_answers", "skipped", "manual", "error", "closed", *APPLIED)
TRACKS = {"qr": "quant research", "qd": "quant dev", "infra": "ML infra / data", "other": "другое"}
LEVEL = {"supported": "[green]●[/]", "partial": "[yellow]◐[/]", "unknown": "[dim]○[/]"}


def _open(cfg: Config) -> DB:
    return DB(cfg.path(cfg.db_path))


def cmd_firms(cfg: Config, args) -> int:
    firms = load_firms(cfg)
    t = Table(title=f"Фирмы ({len(firms)})")
    for col in ("Фирма", "ATS", "Токен", "Tier", "Лимит"):
        t.add_column(col)
    if args.validate:
        t.add_column("Вакансий")
        rows = validate(firms, cfg.watch.workers)
    else:
        rows = [(f, None, "") for f in firms]
    bad = 0
    for f, n, err in rows:
        cells = [f.name, f.ats, f.token, str(f.tier), f"{f.max_applications} за {f.cooldown_months} мес."]
        if args.validate:
            bad += bool(err)
            cells.append(f"[red]{err}[/]" if err else str(n))
        t.add_row(*cells)
    console.print(t)
    return 1 if bad else 0


def cmd_watch(cfg: Config, args) -> int:
    watch.run_once(cfg, _open(cfg), score=not args.no_llm)
    return 0


def cmd_analyze(cfg: Config, args) -> int:
    n = analyst.analyze_new(cfg, _open(cfg))
    console.print(f"Оценено: {n}")
    return 0


def _row_table(rows: list[dict], title: str) -> Table:
    t = Table(title=title)
    for col in ("Балл", "Фирма", "Вакансия", "Тип", "Локация", "Статус", "uid"):
        t.add_column(col, overflow="fold")
    for r in rows:
        t.add_row(str(r["score"] if r["score"] is not None else "—"), r["firm"], r["title"], r["kind"] or "",
                  (r["location"] or "")[:40], r["status"] + (f" ({r['filter_reason']})" if r.get("filter_reason") else ""),
                  r["uid"])
    return t


def cmd_list(cfg: Config, args) -> int:
    db = _open(cfg)
    statuses = args.status.split(",") if args.status else ["scored"]
    rows = [r for r in db.by_status(*statuses, order="score DESC, first_seen DESC")
            if (r["score"] or 0) >= (args.min_score or 0) and (not args.kind or r["kind"] == args.kind)]
    console.print(_row_table(rows[:args.limit], f"{', '.join(statuses)}: {len(rows)}"))
    return 0


def show(cfg: Config, row: dict) -> None:
    conf = row.get("confidence") or {}
    console.rule(f"[bold]{row['firm']}[/] — {row['title']}")
    console.print(f"{row['location']} · {row['kind']} · трек: {TRACKS.get(row.get('track') or '', '—')} · "
                  f"[bold]балл {row['score']}[/]" + (f" · {row['compensation']}" if row.get("compensation") else ""))
    if conf:
        console.print("Уверенность: " + "  ".join(f"{LEVEL.get(v, v)} {k}" for k, v in conf.items()))
    for title, key in (("Почему", "reasons"), ("Риски", "risks"), ("Проверить", "checks")):
        items = row.get(key) or []
        if items:
            console.print(f"[bold]{title}:[/]")
            for x in items:
                console.print(f"  • {x}")
    if row.get("ats_score") is not None:
        console.print(f"ATS-скор: {row['ats_score']} · есть в резюме: {', '.join(row.get('ats_have') or []) or '—'} · "
                      f"нет: {', '.join(row.get('ats_missing') or []) or '—'}")
    console.print(f"[link={row['url']}]{row['url']}[/link]")
    console.print(f"PDF для формы: {cfg.resume_pdf(row.get('track'))}")
    if row.get("letter"):
        console.print("[bold]Письмо:[/]")
        console.print(row["letter"])
        for x in row.get("letter_issues") or []:
            console.print(f"[yellow]⚠ {x}[/]")


def _limit_ok(db: DB, firms: dict, row: dict) -> bool:
    f = firms.get(row["firm"])
    if not f:
        return True
    used = db.applications(f.name, f.cooldown_months)
    if used < f.max_applications:
        return True
    console.print(f"[yellow]В {f.name} уже {used} заявок за {f.cooldown_months} мес. (лимит {f.max_applications}).[/]")
    return input("Всё равно отметить заявку? [y/N] ").strip().lower() == "y"


def cmd_review(cfg: Config, args) -> int:
    if not sys.stdin.isatty():
        raise SystemExit("review — интерактивная команда: запустите её в своём терминале.")
    db = _open(cfg)
    firms = {f.name: f for f in load_firms(cfg)}
    rows = db.for_review(args.min_score or cfg.analyst.min_score, {f.name: f.tier for f in firms.values()})
    if not rows:
        console.print("Нет вакансий для просмотра. Сначала: uv run quant-agent watch")
        return 0
    console.print(f"К просмотру: {len(rows)}. Отклик пока подаёте вы: [o] откроет форму в браузере.")
    for i, row in enumerate(rows, 1):
        console.print(f"\n[dim]{i}/{len(rows)}[/]")
        show(cfg, row)
        while True:
            a = input("[o] открыть форму  [c] письмо в буфер  [a] подал заявку  [s] пропустить  [n] позже  [q] выход > "
                      ).strip().lower()
            if a == "c":
                if row.get("letter"):
                    subprocess.run(["pbcopy"], input=row["letter"].encode(), check=False)
                    console.print("Письмо в буфере обмена.")
                else:
                    console.print("Письма нет: uv run quant-agent letters")
                continue
            if a == "o":
                webbrowser.open(row["apply_url"] or row["url"])
                continue
            if a == "a":
                if _limit_ok(db, firms, row):
                    db.update(row["uid"], status="applied", applied_at=now())
                break
            if a == "s":
                db.update(row["uid"], status="skipped", decided_at=now())
                break
            if a in ("n", ""):
                break
            if a == "q":
                return 0
    return 0


def _resolve(cfg: Config, url: str, firm: str):
    """Вакансия по ссылке. Greenhouse на домене фирмы (…?gh_jid=…) — ищем по доскам Greenhouse из firms.yaml."""
    hit = detect(url)
    if hit and hit[0] == "greenhouse" and not hit[1]:
        for f in load_firms(cfg):
            if f.ats != "greenhouse":
                continue
            try:
                return SOURCES["greenhouse"].fetch_one(f.name, f.token, hit[2])
            except SourceError:
                continue
    job = fetch_url(url, firm)
    if hit and hit[1] and not firm:  # название фирмы из watchlist по токену
        for f in load_firms(cfg):
            if (f.ats, f.token) == (hit[0], hit[1]):
                job.firm = f.name
    return job


def cmd_add(cfg: Config, args) -> int:
    try:
        job = _resolve(cfg, args.url, args.firm or "")
    except SourceError as e:
        raise SystemExit(f"Не удалось получить вакансию: {e}")
    db = _open(cfg)
    kind = classify_kind(job.title, job.hints)
    fields = dict(firm=job.firm, ats=job.ats, token=job.token, ext_id=job.ext_id, title=job.title, location=job.location,
                  kind=kind, url=job.url, apply_url=job.apply_url, department=job.department, description=job.description,
                  compensation=job.compensation, published_at=job.published_at)
    db.seen(job.uid, **fields)
    reason = eligibility(job, kind, cfg.eligibility)  # фильтры не применяем: ссылку дали явно
    if reason:
        db.update(job.uid, status="ineligible", filter_reason=reason)
    else:
        db.update(job.uid, status="new")
        analyst.analyze_new(cfg, db, uids={job.uid})
    show(cfg, db.get(job.uid))
    row = db.get(job.uid)
    console.print(f"Статус: {row['status']}" + (f" — {row['filter_reason']}" if row.get("filter_reason") else ""))
    return 0


def cmd_letters(cfg: Config, args) -> int:
    uids = {args.uid} if args.uid else None
    n = materials.write_letters(cfg, _open(cfg), min_score=args.min_score, uids=uids, force=args.force)
    console.print(f"Написано писем: {n}")
    return 0


def cmd_pdf(cfg: Config, args) -> int:
    path, problems = materials.build_resume_pdf(cfg)
    console.print(f"Резюме: {path}")
    for x in problems:
        console.print(f"[red]ATS-проверка:[/] {x}")
    if not problems:
        console.print("[green]ATS-проверка пройдена[/]: имя, почта и разделы извлекаются из PDF текстом.")
    if args.uid:
        row = _open(cfg).get(args.uid)
        if not row or not row.get("letter"):
            raise SystemExit(f"У {args.uid} нет письма: uv run quant-agent letters --uid {args.uid}")
        console.print(f"Письмо: {materials.build_letter_pdf(cfg, row)}")
    return 1 if problems else 0


def _ask(m: dict) -> str | list[str] | None:
    console.print(f"\n[bold]{m['label']}[/]" + ("" if m["required"] else " [dim](необязательно)[/]"))
    if m["options"]:
        for i, o in enumerate(m["options"], 1):
            console.print(f"  {i}. {o}")
        raw = input("Номер (несколько — через запятую), Enter — пропустить: ").strip()
        if not raw:
            return None
        try:
            picked = [m["options"][int(x) - 1] for x in raw.replace(" ", "").split(",")]
        except (ValueError, IndexError):
            console.print("[red]Нет такого номера — пропускаю.[/]")
            return None
        return picked if len(picked) > 1 else picked[0]
    raw = input("Ответ (Enter — пропустить): ").strip()
    return raw or None


def cmd_answers(cfg: Config, args) -> int:
    """Вопросы анкет, на которые агент не нашёл ответа. Ответ сохраняется в answers.yaml и подходит для
    такого же вопроса в формах других фирм."""
    if not sys.stdin.isatty():
        raise SystemExit("answers — интерактивная команда: запустите её в своём терминале.")
    from . import answers as ans
    from .autopilot import prepare
    db = _open(cfg)
    rows = db.by_status("needs_answers", order="score DESC")
    if not rows:
        console.print("Вопросов без ответа нет.")
        return 0
    asked: set[str] = set()
    for row in rows:
        console.rule(f"{row['firm']} — {row['title']} ({row['score']})")
        plan = ans.Plan.from_json(row["plan"]) if row.get("plan") else None
        for m in plan.missing if plan else []:
            if m["why"].startswith("нет profile.") or m["why"].startswith("нет файла"):
                console.print(f"[yellow]{m['label']}: {m['why']} — заполните config.yaml[/]")
                continue
            if ans.norm(m["label"]) in asked:
                continue
            value = _ask(m)
            if value is not None:
                ans.save_answer(cfg, m["label"], value)
                asked.add(ans.norm(m["label"]))
        db.update(row["uid"], status="scored", error=None)
        if prepare(cfg, db, db.get(row["uid"])) is not None:
            console.print("[green]Все ответы есть — вакансия вернулась в очередь автопилота.[/]")
    return 0


def cmd_apply(cfg: Config, args) -> int:
    from .autopilot import Browser, apply_one, prepare
    db = _open(cfg)
    row = db.get(args.uid)
    if not row:
        raise SystemExit(f"Нет вакансии {args.uid}")
    from .autopilot import APPLIERS
    if row["ats"] not in APPLIERS:
        raise SystemExit(f"Автозаполнение есть для {', '.join(APPLIERS)}; {row['ats']} — пока нет.")
    if not row.get("letter"):
        materials.write_letters(cfg, db, uids={row["uid"]})
        row = db.get(row["uid"])
    plan = prepare(cfg, db, row)
    if plan is None:
        return 1
    browser = Browser(cfg)
    try:
        status = apply_one(cfg, db, browser.page(), row, plan, args.mode)
    finally:
        browser.close()
    return 0 if status in ("applied", "dry_run") else 1


def cmd_autopilot(cfg: Config, args) -> int:
    from . import autopilot
    mode = args.mode or cfg.apply.mode
    if mode == "assist":
        console.print("Режим assist: агент заполняет формы, Submit нажимаете вы в окне браузера.")
    autopilot.run(cfg, _open(cfg), mode)
    return 0


def cmd_mark(cfg: Config, args) -> int:
    db = _open(cfg)
    row = db.get(args.uid)
    if not row:
        raise SystemExit(f"Нет вакансии {args.uid}")
    fields = {"status": args.status, "decided_at": now()}
    if args.status == "applied" and not row.get("applied_at"):
        fields["applied_at"] = now()
    if args.note:
        fields["note"] = args.note
    db.update(args.uid, **fields)
    console.print(f"{row['firm']} — {row['title']}: {row['status']} → {args.status}")
    return 0


def cmd_mail(cfg: Config, args) -> int:
    """Проверка почты: вход по IMAP и последние письма с кодами за args.days дней."""
    from datetime import UTC, datetime, timedelta

    from . import mail
    since = datetime.now(UTC) - timedelta(days=args.days)
    try:
        box = mail.connect(cfg)
    except mail.MailError as e:
        console.print(f"[red]Почта не настроена:[/] {e}")
        return 1
    try:
        letters = [x for f in cfg.mail.folders for x in mail.recent(box, f, since, limit=200)]
    finally:
        box.logout()
    console.print(f"[green]Вход в {cfg.mail.imap_host} — ок.[/] Писем за {args.days} дн.: {len(letters)}")
    for x in letters:
        code = mail.extract_code(x.subject, x.body)
        if code:
            console.print(f"  {x.date:%Y-%m-%d %H:%M}  {x.subject[:70]}  → код {code}")
    return 0


def cmd_stats(cfg: Config, args) -> int:
    db = _open(cfg)
    counts = db.counts()
    console.print("Вакансии: " + ", ".join(f"{k}: {counts[k]}" for k in STATUSES if k in counts))
    t = Table(title="Здоровье досок (последний опрос)")
    for col in ("Фирма", "ATS", "Когда", "Вакансий", "Ошибок подряд", "Ошибка"):
        t.add_column(col, overflow="fold")
    for h in db.health():
        t.add_row(h["firm"], h["ats"], h["ts"], str(h["count"]) if h["ok"] else "—",
                  str(h["fails_in_row"]) if h["fails_in_row"] else "", f"[red]{h['error']}[/]" if h["error"] else "")
    console.print(t)
    return 0


HELP = """Команды:
  firms [--validate]           список фирм; --validate — проверить, что доски отвечают
  watch [--no-llm]             опросить доски, оценить новые вакансии, выйти
  analyze                      оценить новые вакансии через LLM
  list [--status S] [--min-score N] [--kind K]   вакансии в базе
  review                       просмотр лучших вакансий и отметка заявок (в своём терминале)
  add <url> [--firm NAME]      одна вакансия по ссылке: Greenhouse, Lever, Ashby или любая страница
  letters [--min-score N] [--uid UID] [--force]   письма для вакансий с баллом ≥ analyst.min_score
  pdf [--uid UID]              PDF резюме (materials/resume.pdf) с ATS-проверкой; --uid — ещё и письмо в PDF
  answers                      ответить на вопросы анкет, которых агент не знает (сохраняются в answers.yaml)
  apply <uid> [--mode M]       заполнить форму одной вакансии (assist | auto | dry_run)
  autopilot [--mode M]         проход: опрос в фоне, формы подряд из очереди, выход
  mark <uid> <status>          поменять статус: applied, oa, interview, offer, rejected, skipped…
  mail [--days N]              проверить почту (IMAP): вход и найденные коды подтверждения
  stats                        статусы вакансий и здоровье досок"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="quant-agent", description="Стажировки и graduate-позиции в HFT и квант-фондах")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("help")
    s = sub.add_parser("firms")
    s.add_argument("--validate", action="store_true")
    s = sub.add_parser("watch")
    s.add_argument("--once", action="store_true", help=argparse.SUPPRESS)  # старый флаг: проход и так один
    s.add_argument("--no-llm", action="store_true")
    sub.add_parser("analyze")
    s = sub.add_parser("list")
    s.add_argument("--status")
    s.add_argument("--min-score", type=int)
    s.add_argument("--kind", choices=["internship", "graduate", "full_time"])
    s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("review")
    s.add_argument("--min-score", type=int)
    s = sub.add_parser("add")
    s.add_argument("url")
    s.add_argument("--firm")
    s = sub.add_parser("letters")
    s.add_argument("--min-score", type=int)
    s.add_argument("--uid")
    s.add_argument("--force", action="store_true")
    s = sub.add_parser("pdf")
    s.add_argument("--uid")
    sub.add_parser("answers")
    s = sub.add_parser("apply")
    s.add_argument("uid")
    s.add_argument("--mode", choices=["assist", "auto", "dry_run"])
    s = sub.add_parser("autopilot")
    s.add_argument("--mode", choices=["assist", "auto", "dry_run"])
    s.add_argument("--once", action="store_true", help=argparse.SUPPRESS)  # старый флаг: проход и так один
    s = sub.add_parser("mark")
    s.add_argument("uid")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--note")
    s = sub.add_parser("mail")
    s.add_argument("--days", type=int, default=3)
    sub.add_parser("stats")
    args = p.parse_args(argv)
    if args.cmd in (None, "help"):
        console.print(HELP)
        return 0
    stop.install()
    cfg = load_config(args.config)
    cmds = {"firms": cmd_firms, "watch": cmd_watch, "analyze": cmd_analyze, "list": cmd_list, "review": cmd_review,
            "add": cmd_add, "letters": cmd_letters, "answers": cmd_answers, "apply": cmd_apply, "autopilot": cmd_autopilot, "pdf": cmd_pdf, "mark": cmd_mark, "mail": cmd_mail,
            "stats": cmd_stats}
    try:
        return cmds[args.cmd](cfg, args)
    except (KeyboardInterrupt, stop.Stopped):
        console.print("\nОстановлено.")
        return stop.exit_code()

