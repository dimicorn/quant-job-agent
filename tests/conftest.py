"""Конфиг из config.example.yaml, firms.yaml и база — во временной папке теста: ваши config.yaml и база
не трогаются. Сеть и LLM в тестах подменяются."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quant_agent import llm
from quant_agent.config import load_config
from quant_agent.db import DB

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"

RESUME = """# Candidate — ML engineer
MIPT, MSc 2026. Python, C++17, PyTorch, CatBoost, statistics, probability, time series, Linux, Docker.
Real-time CV pipeline (~30 FPS), quantization INT8, first-author ICML workshop poster."""


@pytest.fixture
def cfg(tmp_path):
    shutil.copy(ROOT / "config.example.yaml", tmp_path / "config.yaml")
    shutil.copy(ROOT / "firms.yaml", tmp_path / "firms.yaml")
    (tmp_path / "resume_en.md").write_text(RESUME, encoding="utf-8")
    c = load_config(tmp_path / "config.yaml")
    c.watch.notify = False
    c.watch.aggregators = []  # без сети: списки SimplifyJobs подставляют тесты сами
    return c


@pytest.fixture
def db(cfg):
    return DB(cfg.path(cfg.db_path))


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def _fresh_llm_state():
    """Лимиты бэкендов и форматы ответа — глобальное состояние модуля llm."""
    llm._limited = None
    llm._FORMAT.clear()
    yield
    llm._limited = None
