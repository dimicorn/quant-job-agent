# quant-job-agent

Агент поиска стажировок и graduate-позиций в HFT, у маркет-мейкеров и в квант-фондах Европы и UK.
Младший брат [hh-agent](https://github.com/dimicorn/ai-job-search-ru): тот же LLM-слой, но другие площадки и другая
модель работы — фирм несколько десятков, попыток в каждую мало, поэтому важнее выбрать, куда подавать, чем подать везде.

```
firms.yaml → доски ATS → тип вакансии → фильтры → условия допуска → оценка LLM → review / autopilot
```

## Установка

```bash
uv sync && uv run playwright install chromium
cp config.example.yaml config.yaml      # заполнить eligibility, preferences, profile, резюме
uv run quant-agent firms --validate     # доски отвечают?
```

## Использование

```bash
uv run quant-agent help           # все команды и опции
uv run quant-agent watch          # опросить доски и оценить новые вакансии
uv run quant-agent review         # лучшие вакансии; [o] открыть форму, [a] отметить заявку
uv run quant-agent add <url>      # одна вакансия по ссылке
uv run quant-agent autopilot      # опрос в фоне и заявки подряд из очереди (режим из apply.mode)
uv run quant-agent answers        # ответить на вопросы анкет, которых агент не знает
uv run quant-agent stats          # статусы вакансий и здоровье досок
```

## Подробнее

- [Источники и оценка](docs/sources.md) — доски ATS, фирмы без ATS, агрегаторы, условия допуска, оценка LLM, лимит на фирму.
- [Автопилот](docs/autopilot.md) — режимы, план ответов и `answers.yaml`, заполнение форм, браузер, код из письма, лимиты.
- [Если сломалось](docs/troubleshooting.md) — капча, `manual`, `needs_answers`, лимиты LLM, где смотреть логи.

## Что важно знать

- `review` и `answers` интерактивные: запускайте их в обычном терминале.
- Форма, которую агент не смог заполнить, остаётся вам во вкладке браузера — агент её не трогает.
- Личное (`config.yaml`, резюме, `answers.yaml`, база, `.env`, `logs/`) — в `.gitignore`.

## Разработка

Линтер, тесты, CI, стиль коммитов и релизы — в [CONTRIBUTING.md](CONTRIBUTING.md), история — в [CHANGELOG.md](CHANGELOG.md).

## Лицензия

[MIT](LICENSE).
