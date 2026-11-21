# Разработка quant-job-agent

## Окружение

```bash
uv sync && uv run playwright install chromium
```

`uv sync` ставит и dev-зависимости (ruff, pytest).

## Проверки

- **Линтер** — ruff, правила в `pyproject.toml`: `uv run ruff check .`, автоисправление — `--fix`.
- **Тесты** — pytest: `uv run pytest`. Сеть и LLM в тестах подменены: ответы досок — в `tests/fixtures/`
  (сохранены с живых досок и обрезаны), конфиг и база — во временной папке (`tests/conftest.py`), поэтому
  ваши `config.yaml`, `answers.yaml` и база не трогаются. Формы Greenhouse, Lever, Ashby и Workable
  проверяются в настоящем Chromium на подставных страницах (`page.route`, без сети).
- **CI** — на каждый push в `main` и pull request GitHub Actions (`.github/workflows/ci.yml`) запускает ruff
  и тесты на Ubuntu и macOS.

## Коммиты

Сообщения коммитов — по [Conventional Commits](https://www.conventionalcommits.org/ru/):
`feat: …`, `fix: …`, `perf: …`, `refactor: …`, `docs: …`, `test: …`, `ci: …`, `chore: …`,
при желании с областью: `fix(apply): …`; несовместимое изменение — `feat(autopilot)!: …`.
По префиксу коммит попадает в раздел CHANGELOG (🚀 Новое, 🐛 Исправления, …), первая строка сообщения —
текст записи, а коммиты без префикса в CHANGELOG не попадают.

## Релизы и CHANGELOG

Версию пакета задаёт git-тег (hatch-vcs): на теге `v0.2.0` это `0.2.0`, после него —
`0.2.1.devN+g<коммит>`; в `pyproject.toml` её править не нужно.

`main` защищён (ruleset «main через PR»): изменения попадают в него только через PR, в котором прошли
проверки CI (Ruff и тесты на Ubuntu и macOS); force-push и удаление `main` запрещены. PR сливается только
merge-коммитом (squash и rebase выключены): коммиты ветки попадают в `main` как есть, поэтому каждый из них пишется
по Conventional Commits — в CHANGELOG это отдельные записи, а сам «Merge pull request…» git-cliff пропускает.

CHANGELOG.md собирает git-cliff по `cliff.toml` из истории коммитов. Релиз — тоже через PR:

```bash
git switch -c release/v0.2.0
uvx git-cliff --tag v0.2.0 -o CHANGELOG.md
git commit -am "chore(release): v0.2.0" && git push -u origin release/v0.2.0
gh pr create --fill
# после слияния PR:
git switch main && git pull
git tag -a v0.2.0 -m v0.2.0 && git push origin v0.2.0
```

На push тега workflow `.github/workflows/release.yml` публикует GitHub Release с разделом этой версии
из CHANGELOG. Посмотреть заранее, что попадёт в релиз: `uvx git-cliff --unreleased -o -`
(без `-o -` git-cliff перезапишет CHANGELOG.md).
