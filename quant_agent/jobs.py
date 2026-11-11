"""Вакансия и всё, что о ней решается без LLM: тип (стажировка / выпускники / штат), локальные фильтры,
условия допуска (учёба, год выпуска, виза) и ATS-скор — совпадение ключевых слов с резюме."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .config import Eligibility, Filters, Kind


@dataclass
class Job:
    firm: str
    ats: str
    token: str
    ext_id: str
    title: str
    location: str = ""
    url: str = ""
    apply_url: str = ""
    department: str = ""
    description: str = ""
    compensation: str = ""
    published_at: str = ""
    hints: list[str] = field(default_factory=list)  # «Campus», «Intern» и т.п. из полей ATS

    @property
    def uid(self) -> str:
        return f"{self.ats}:{self.token}:{self.ext_id}"


def html_to_text(s: str) -> str:
    """HTML описания → текст. Greenhouse отдаёт HTML ещё и экранированным — снимаем оба слоя."""
    s = html.unescape(s or "")
    s = re.sub(r"<(br|/p|/li|/h\d|/div)[^>]*>", "\n", s)
    s = re.sub(r"<li[^>]*>", "• ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


# ── Тип вакансии ──────────────────────────────────────────────────────

_INTERN = re.compile(r"\bintern(ship)?s?\b|\binternship\b|summer (analyst|associate|program)|off[- ]cycle|"
                     r"\bplacement\b|\bwerkstudent|\bstagiaire\b|\bstage\b|praktikum|\btrainee\b", re.I)
_GRAD = re.compile(r"\bgraduate\b|new grad|\bcampus\b|early careers?|entry[- ]level|\bjunior\b|\bgrad\b|"
                   r"\bphd\b.*\b(full[- ]time|researcher)\b|\b20\d\d start\b|university hire", re.I)
_SENIOR = re.compile(r"\b(senior|sr\.?|lead|principal|staff|head|director|manager|vp|vice president)\b", re.I)


def classify_kind(title: str, hints: list[str] | None = None) -> Kind:
    """По названию и подсказкам ATS (Workflow «Campus» у Greenhouse, «Intern» у Lever/Ashby)."""
    text = " ".join([title, *(hints or [])])
    if _INTERN.search(text):
        return "internship"
    if _GRAD.search(text) and not _SENIOR.search(title):
        return "graduate"
    return "full_time"


# ── Локальные фильтры ────────────────────────────────────────────────

def prefilter(job: Job, kind: Kind, f: Filters, kinds: list[Kind] | None = None) -> str | None:
    """Причина отсева или None. kinds — типы вакансий фирмы из firms.yaml вместо filters.kinds.
    Вакансию без локации (свои сайты фирм часто её не пишут) по локации не отсеиваем: решит LLM."""
    title = job.title.lower()
    kinds = kinds or f.kinds
    if kinds and kind not in kinds:
        return f"тип {kind} не в filters.kinds"
    if f.title_include_any and not any(w.lower() in title for w in f.title_include_any):
        return "в названии нет обязательных слов"
    for w in f.title_exclude:
        if w.lower() in title:
            return f"стоп-слово в названии «{w}»"
    if f.locations and job.location.strip():
        loc = job.location.lower()
        if not any(x.lower() in loc for x in f.locations):
            return f"локация «{job.location}» не в filters.locations"
    return None


# ── Условия допуска ──────────────────────────────────────────────────

_ENROLLED = re.compile(
    r"currently (enrolled|pursuing|studying|completing)|pursuing an? (bachelor|master|ph\.?d|degree|undergraduate)|"
    r"returning to (school|university|your studies)|enrolled (in|at) an? (accredited |full[- ]time )?(degree|university|"
    r"bachelor|master|ph\.?d|program)|penultimate[- ]year|must be a (current )?student|current (university )?students?\b",
    re.I)
# «Graduate … (2027 Start)», «join the graduate programme in June 2027» — год начала работы, а не выпуска:
# нужен глагол («to graduate in 2027», «graduate by 2028») или «class of».
# Диапазоны: «2027 or 2028», «between May 2025 and February 2028», «Fall 2027 and Summer 2028»;
# «by 2027» — не позже; «2024 or later» — не раньше.
_GRAD_YEARS = re.compile(
    r"(?:graduating|graduation|graduated|graduate(?=\s+(?:in|by|before|between|from|after|during|no|prior|until)\b)"
    r"|class of|degree completion|complet\w* (?:your|their|a) degree)"
    r"(?P<gap>[^.\n]{0,60}?)(?P<lo>20\d\d)"
    r"(?:\s*(?:-|–|—|or|and|to|through|until)\s*(?:[A-Za-z]+\.?\s+)?(?P<hi>20\d\d))?"
    r"(?P<later>\s*(?:or|and) (?:later|after|beyond))?", re.I)
_BY = re.compile(r"\b(by|before|no later than|prior to)\b", re.I)
_AFTER = re.compile(r"\b(after|since|from)\b", re.I)


def graduation_range(text: str) -> tuple[int, int] | None:
    """Допустимые годы выпуска по тексту вакансии или None, если явного требования нет."""
    m = _GRAD_YEARS.search(text or "")
    if not m:
        return None
    y1, y2 = int(m["lo"]), int(m["hi"] or m["lo"])
    if m["later"] or (_AFTER.search(m["gap"]) and not m["hi"]):
        return y1, 9999
    if _BY.search(m["gap"]) and not m["hi"]:
        return 0, y1
    return min(y1, y2), max(y1, y2)
_NO_SPONSOR = re.compile(
    r"(?:not|unable to|cannot|can't|won't|will not|do not|does not|don't) (?:currently )?(?:able to )?"
    r"(?:offer|provide|support)? ?(?:visa |work permit |immigration )?sponsor|"
    r"without (?:the need for |requiring )?(?:current or future )?(?:visa |employer )?sponsorship|"
    r"no (?:visa )?sponsorship|must (?:already )?have (?:the )?(?:existing |full )?right to work|"
    r"existing right to work", re.I)


def eligibility(job: Job, kind: Kind, e: Eligibility) -> str | None:
    """Почему не подходите по формальным условиям (или None). Только явные формулировки в тексте:
    сомнительные случаи решает LLM при оценке и показывает в рисках."""
    text = f"{job.title}\n{job.description}"
    if kind == "internship" and not e.enrolled and _ENROLLED.search(text):
        return "стажировка только для студентов вуза (eligibility.enrolled: false)"
    if e.graduation_year:
        rng = graduation_range(job.description)  # только текст: в названии год — обычно год начала работы
        if rng and not rng[0] <= e.graduation_year <= rng[1]:
            lo, hi = rng
            yrs = f"не раньше {lo}" if hi == 9999 else f"не позже {hi}" if lo == 0 else f"{lo}–{hi}" if hi != lo else str(lo)
            return f"нужен выпуск {yrs}, а у вас {e.graduation_year}"
    if e.needs_sponsorship and _NO_SPONSOR.search(text):
        return "фирма не спонсирует рабочую визу"
    for country in e.restricted_countries:
        if _restricted(country).search(text):
            return f"вакансия не рассматривает кандидатов из страны «{country}»"
    return None


_SANCTIONED = r"(?:sanctioned|embargoed|comprehensively sanctioned) (?:countries|jurisdictions|regions)"


def _restricted(country: str) -> re.Pattern:
    """Явный запрет на кандидатов из страны: «unable to consider candidates (based|located|residing) in Russia»,
    «not open to applicants from Russia», «residents of sanctioned countries»."""
    c = re.escape(country)
    return re.compile(
        rf"(?:unable to|cannot|can't|not able to|do not|don't|will not|won't|not) (?:currently )?(?:consider|accept|hire|"
        rf"employ|open to|process)[^.\n]{{0,60}}?(?:{c}|{_SANCTIONED})|"
        rf"(?:candidates|applicants|residents|individuals|persons)[^.\n]{{0,40}}?(?:in|from|of) (?:{c}|{_SANCTIONED})"
        rf"[^.\n]{{0,40}}?(?:not|ineligible|excluded|cannot)", re.I)


# ── ATS-скор: ключевые слова вакансии против резюме, без LLM ──────────

# термин → варианты написания (регулярки, без учёта регистра)
KEYWORDS: dict[str, str] = {
    "C++": r"c\+\+(?:1[147]|2[03])?", "Python": r"python", "Rust": r"\brust\b", "Java": r"\bjava\b", "Go": r"\bgolang\b",
    "C#": r"c#", "OCaml": r"ocaml", "Haskell": r"haskell", "kdb+/q": r"\bkdb\+?", "SQL": r"\bsql\b",
    "Linux": r"linux", "Bash": r"\bbash\b", "Git": r"\bgit\b", "Docker": r"docker", "Kubernetes": r"kubernetes|\bk8s\b",
    "CUDA": r"\bcuda\b", "GPU": r"\bgpus?\b", "FPGA": r"\bfpgas?\b", "Verilog": r"verilog", "VHDL": r"\bvhdl\b",
    "low latency": r"low[- ]latency", "networking": r"network(?:ing)? (?:stack|protocols?)|\btcp\b|\budp\b",
    "multithreading": r"multi[- ]?thread|concurren(?:cy|t)|многопоточ", "distributed systems": r"distributed (?:systems|computing)|распредел[её]нн\w* систем",
    "performance optimization": r"performance (?:optimi[sz]|tuning|engineering)|profiling",
    "statistics": r"statistic|статистик", "probability": r"probabilit|вероятност", "machine learning": r"machine learning|\bml\b|машинн\w* обучени",
    "deep learning": r"deep learning|neural net|нейронн\w* сет|нейросет", "time series": r"time[- ]series|временн\w* ряд", "optimization": r"\boptimi[sz]ation\b|оптимизац",
    "reinforcement learning": r"reinforcement learning|обучени\w* с подкреплением", "signal processing": r"signal processing|обработк\w* (?:аудио и )?сигнал",
    "Bayesian": r"bayesian", "stochastic": r"stochastic", "linear algebra": r"linear algebra|линейн\w* алгебр",
    "PyTorch": r"pytorch", "TensorFlow": r"tensorflow", "JAX": r"\bjax\b", "NumPy": r"numpy", "pandas": r"pandas",
    "scikit-learn": r"scikit|sklearn", "Spark": r"\bspark\b", "Kafka": r"\bkafka\b", "Airflow": r"airflow",
    "data pipelines": r"data pipelines?|\betl\b", "MLOps": r"mlops|model serving|inference", "cloud": r"\baws\b|\bgcp\b|azure",
    "options": r"\boptions? (?:pricing|trading|market)|derivatives", "market making": r"market[- ]making|market maker",
    "mathematics": r"mathemat|математ", "physics": r"physics|физик", "PhD": r"ph\.?d", "competitive programming": r"olympiad|icpc|competitive programming|олимпиад",
}
_KW = {k: re.compile(v, re.I) for k, v in KEYWORDS.items()}


def ats_score(description: str, resume: str) -> tuple[int | None, list[str], list[str]]:
    """(балл 0–100 или None, есть в резюме, нет в резюме) по термам из KEYWORDS, найденным в вакансии.
    Похоже на ATS-фильтры фирм: грубо, зато бесплатно и одинаково для всех вакансий."""
    found = [k for k, rx in _KW.items() if rx.search(description or "")]
    if not found:
        return None, [], []
    have = [k for k in found if _KW[k].search(resume or "")]
    missing = [k for k in found if k not in have]
    return round(100 * len(have) / len(found)), have, missing
