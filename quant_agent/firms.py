"""Watchlist фирм (firms.yaml) и проверка, что их доски отвечают."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .config import Config


class Firm(BaseModel):
    name: str
    ats: Literal["greenhouse", "lever", "ashby", "workable", "site"]
    token: str                     # доска ATS; для site — адрес страницы карьеры
    links: str = ""                # site: регулярка путей страниц вакансий («^/jobs/»); пусто — вся страница одной записью
    tier: int = 2
    max_applications: int = 2      # заявок за cooldown_months
    cooldown_months: int = 6
    season: list[int] = Field(default_factory=list)  # месяцы набора стажёров/выпускников
    kinds: list[Literal["internship", "graduate", "full_time"]] = Field(default_factory=list)  # вместо filters.kinds


def load_firms(cfg: Config) -> list[Firm]:
    path = cfg.path(cfg.firms_file)
    if not path.exists():
        raise SystemExit(f"Не найден список фирм: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults") or {}
    firms = [Firm.model_validate({**defaults, **f}) for f in data.get("firms") or []]
    names = [f.name for f in firms]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SystemExit(f"{path.name}: фирмы повторяются: {', '.join(sorted(dup))}")
    return firms


def by_name(firms: list[Firm]) -> dict[str, Firm]:
    return {f.name: f for f in firms}


def validate(firms: list[Firm], workers: int = 6) -> list[tuple[Firm, int | None, str]]:
    """(фирма, число вакансий или None, ошибка) по каждой доске."""
    from .sources import SourceError, fetch_firm

    def one(f: Firm):
        try:
            return f, len(fetch_firm(f)), ""
        except SourceError as e:
            return f, None, str(e)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        return list(ex.map(one, firms))
