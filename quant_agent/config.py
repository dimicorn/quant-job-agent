"""Настройки (config.yaml). Относительные пути считаются от папки конфига: cfg.path()."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

Kind = Literal["internship", "graduate", "full_time"]


class Analyst(BaseModel):
    """LLM: те же бэкенды, что в hh-agent (llm.py)."""
    backend: str = "codex"  # codex | claude_code | api | openai | command
    model: str = "claude-opus-5-5"  # модель для claude_code / api / openai
    effort: str = "medium"
    base_url: str | None = None   # openai: адрес API
    api_key_env: str | None = "OPENAI_API_KEY"  # openai: переменная (или строка в .env) с ключом
    openai_extra: dict[str, Any] = Field(default_factory=dict)
    command: str | None = None    # command: программа, которая читает запрос из stdin и печатает JSON
    fallback_backend: str | None = None  # кончились лимиты основного — запросы идут сюда
    fallback_settings: dict[str, Any] = Field(default_factory=dict)
    codex_model: str | None = None
    codex_effort: str | None = None
    batch_size: int = 6           # вакансий на один вызов оценки
    parallel: int = 3             # одновременных запросов к LLM
    min_score: int = 70           # в review и в уведомления попадают вакансии с баллом не ниже
    resume_file: str = "resume_en.md"
    preferences: str = ""


class Filters(BaseModel):
    """Локальные фильтры до LLM (бесплатно). Слова и локации — подстрокой, без учёта регистра."""
    kinds: list[Kind] = Field(default_factory=lambda: ["graduate", "internship"])
    locations: list[str] = Field(default_factory=list)  # пусто — любые
    title_include_any: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)


class Eligibility(BaseModel):
    """Условия допуска. Проверяются по тексту вакансии без LLM; LLM при оценке видит их тоже."""
    enrolled: bool = False                 # учитесь ли сейчас в вузе (стажировки часто требуют)
    graduation_year: int | None = None     # год выпуска (последней степени или ожидаемый)
    needs_sponsorship: bool = True         # нужна рабочая виза (UK/EU) — «no sponsorship» отсекает
    current_location: str = ""             # где вы живёте сейчас — агент указывает только это место
    citizenship: list[str] = Field(default_factory=list)
    work_authorization: list[str] = Field(default_factory=list)  # страны, где можно работать без визы
    restricted_countries: list[str] = Field(default_factory=list)  # вакансия явно не берёт кандидатов отсюда → ineligible
    note: str = ""                         # свободный текст для LLM: «поступаю в аспирантуру…»


class Watch(BaseModel):
    every_minutes: int = 90                # как часто опрашивать доски фирм
    notify: bool = True                    # уведомление macOS о новой подходящей вакансии
    workers: int = 6                       # параллельных запросов к доскам
    # списки SimplifyJobs (listings.json): стажировки и вакансии для выпускников из сотен фирм, категория Quant
    aggregators: list[str] = Field(default_factory=lambda: [
        "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
        "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json"])
    aggregator_details: int = 30           # сколько новых вакансий из списков дочитывать за опрос (текст вакансии)


class Education(BaseModel):
    """Блок образования в форме Greenhouse. Названия — как в справочниках Greenhouse."""
    school: str                            # «Moscow Institute of Physics and Technology» (регистр не важен)
    school_aliases: list[str] = Field(default_factory=list)  # другие названия: справочники вузов у фирм разные
    degree_aliases: list[str] = Field(default_factory=list)  # «Master» вместо «Master's Degree» и т.п.
    discipline_aliases: list[str] = Field(default_factory=list)
    degree: str                            # «Master's Degree», «Bachelor's Degree», «Doctor of Philosophy (Ph.D.)»
    discipline: str                        # «Computer Science», «Physics»…
    start: tuple[int, int]                 # (месяц, год)
    end: tuple[int, int]


class Profile(BaseModel):
    """Данные для стандартных полей формы. Только правда: агент подставляет их как есть."""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""                        # в международном формате: +7 926 …
    country: str = ""                      # страна проживания — как в списке формы («Russia»)
    city: str = ""                         # город — как в подсказке поля Location («Moscow, Moscow City, Russia»)
    linkedin: str = ""
    github: str = ""
    website: str = ""
    education: list[Education] = Field(default_factory=list)  # первый — последний по времени


class Apply(BaseModel):
    """Отклики через формы ATS (quant-agent apply / autopilot). Паузы — как в hh-agent (apply.auto)."""
    mode: Literal["assist", "auto", "dry_run"] = "assist"  # assist — агент заполняет, Submit жмёте вы
    daily_limit: int = 5                   # заявок в сутки (у фирм мало попыток — больше не нужно)
    max_per_run: int = 5
    min_score: int = 80                    # автопилот подаёт только на вакансии с баллом не ниже
    auto_consent: bool = True              # вопросы с единственным вариантом («I acknowledge…») — выбрать его
    answers_file: str = "answers.yaml"     # ваши ответы на вопросы анкет; пополняется командой answers
    profile_dir: str = ".browser-profile"
    headless: bool = False
    submit_wait_minutes: int = 20          # assist: сколько ждать, пока вы нажмёте Submit
    active_hours: tuple[int, int] = (8, 23)
    weekdays_only: bool = False
    read_seconds: tuple[float, float] = (8, 20)       # «читаем» вакансию перед заполнением
    action_seconds: tuple[float, float] = (0.8, 2.5)  # пауза между полями
    typing_delay_ms: tuple[int, int] = (30, 90)       # печать посимвольно; (0, 0) — вставлять целиком
    delay_seconds: tuple[float, float] = (90, 240)    # пауза между заявками
    max_consecutive_errors: int = 3


class Mail(BaseModel):
    """Почта для кодов подтверждения: Greenhouse после Submit присылает security code на email анкеты.
    IMAP; для Gmail — пароль приложения (myaccount.google.com/apppasswords), не обычный пароль."""
    imap_host: str = "imap.gmail.com"
    user: str = ""                         # "" — profile.email
    password_env: str = "IMAP_PASSWORD"    # переменная окружения или строка в .env рядом с config.yaml
    folders: list[str] = Field(default_factory=lambda: ["INBOX"])
    code_wait_seconds: int = 180           # сколько ждать письмо с кодом


class Config(BaseModel):
    analyst: Analyst = Field(default_factory=Analyst)
    filters: Filters = Field(default_factory=Filters)
    eligibility: Eligibility = Field(default_factory=Eligibility)
    watch: Watch = Field(default_factory=Watch)
    profile: Profile = Field(default_factory=Profile)
    apply: Apply = Field(default_factory=Apply)
    mail: Mail = Field(default_factory=Mail)
    facts: str = ""                        # факты о вас, которых нет в резюме
    hh_agent_dir: str = ""                 # папка hh-agent: его questions.facts и faq.yaml — дополнительные факты
    # PDF резюме для загрузки в форму по треку (qr | qd | infra | other); нет трека — materials/resume.pdf
    resume_pdfs: dict[str, str] = Field(default_factory=dict)
    firms_file: str = "firms.yaml"
    db_path: str = "quant_agent.db"

    base_dir: Path = Path(".")

    def path(self, p: str) -> Path:
        q = Path(p).expanduser()
        return q if q.is_absolute() else self.base_dir / q

    def secret(self, name: str) -> str:
        """Значение из окружения, иначе из .env рядом с config.yaml (launchd не видит переменных из ~/.zshrc)."""
        if not name:
            return ""
        if os.environ.get(name):
            return os.environ[name]
        env = self.path(".env")
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                k, _, v = line.partition("=")
                if k.strip().removeprefix("export ").strip() == name:  # и «export KEY=…» (для source .env)
                    return v.strip().strip('"').strip("'")
        return ""

    def resume_text(self) -> str:
        f = self.path(self.analyst.resume_file)
        if not f.exists():
            raise SystemExit(f"Не найдено резюме: {f}. Скопируйте resume.example.md в {f.name} и заполните.")
        return f.read_text(encoding="utf-8")

    def resume_pdf(self, track: str | None) -> Path:
        p = self.resume_pdfs.get(track or "") or self.resume_pdfs.get("other")
        return self.path(p) if p else self.path("materials/resume.pdf")


def load_config(path: str | Path) -> Config:
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise SystemExit(f"Не найден конфиг: {path}. Скопируйте config.example.yaml в config.yaml.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(data)
    cfg.base_dir = path.parent
    return cfg
