# Источники и оценка

```
firms.yaml → доски ATS (публичный JSON API) → тип вакансии → фильтры → условия допуска → оценка LLM → review
```

## Источники

- **Доски ATS** — Greenhouse, Lever, Ashby, Workable: публичные API, без логина, браузера и капчи.
  Доски в `firms.yaml` проверены на живых данных; проверить заново — `quant-agent firms --validate`.
- **Фирмы без ATS** (Alber Blanc, Wunder Fund, QST, SPECTRAL…) — `ats: site`: страница карьеры и ссылки на вакансии
  по шаблону `links`, либо слежение за изменениями всей страницы.
- **Агрегаторы** — списки SimplifyJobs на GitHub (`watch.aggregators`): quant-стажировки и позиции для выпускников
  из фирм, которых нет в `firms.yaml` (Workday, iCIMS — подача вручную).
- **Одна вакансия по ссылке** — `quant-agent add <url>`: Greenhouse, Lever, Ashby, Workable или любая страница.

Workday и письма-алерты LinkedIn/eFC — следующие шаги. getmatch.ru — через Telegram-бота в hh-agent.

## Тип вакансии и фильтры

Тип — `internship | graduate | full_time`, по названию и полям ATS («Campus», «Summer Internship»…).
По умолчанию оцениваются только стажировки и позиции для выпускников (`filters.kinds`). У фирмы в `firms.yaml`
можно задать свои `kinds` — например, брать и junior full-time у русскоязычных команд. Фильтры по городам и
словам в названии (`filters.locations`, `title_include_any`, `title_exclude`) срабатывают до LLM и ничего не стоят.

## Условия допуска без LLM

Секция `eligibility` отсекает то, на что подавать бессмысленно: «только для студентов вуза», годы выпуска
(«between May 2025 and February 2028», «graduating by 2027»), «no visa sponsorship», явный запрет на кандидатов
из страны. Не подходит — статус `ineligible` с причиной, попытку в фирму не тратим. Сомнительные случаи LLM видит
и решает при оценке.

## Оценка LLM

- Балл 0–100 и трек (quant research / quant dev / ML infra).
- **Уверенность по каждому измерению отдельно от балла**, до трёх вещей «что проверить», риски, блокер.
- Тексты вакансий передаются как данные, инструкции в них игнорируются.
- Местоположение кандидата — только из `eligibility.current_location`.
- В `review` и уведомления попадают вакансии с баллом ≥ `analyst.min_score`.

LLM выбирается в `analyst.backend`: Codex CLI, Claude Code (`claude -p`), Claude API, любой OpenAI-совместимый API
или своя программа; `fallback_backend` — запасная на случай лимитов. Слой LLM общий с hh-agent.

**ATS-скор без LLM** — какие ключевые слова вакансии есть в резюме, а каких нет.

## Жизнь вакансии

- Вакансия пропала с доски — `closed`; вернулась — прежний статус.
- Ошибка доски не закрывает вакансии и видна в `stats` (здоровье источников). Сломанный ответ одной доски не
  останавливает опрос остальных.
- **Лимит заявок на фирму** — `max_applications` за `cooldown_months`; `review` предупреждает перед лишней заявкой.
- Статус вручную: `quant-agent mark <uid> applied | oa | interview | offer | rejected | skipped…`.
