# quant-job-agent

Агент поиска стажировок и graduate-позиций в HFT, у маркет-мейкеров и в квант-фондах Европы и UK.
Младший брат [hh-agent](../hh-agent): тот же LLM-слой (`llm.py`), но другие площадки и другая модель работы —
фирм несколько десятков, попыток в каждую мало, поэтому важнее выбрать, куда подавать, чем подать везде.

## Как работает

```
firms.yaml → доски ATS (публичный JSON API) → тип вакансии → фильтры → условия допуска → оценка LLM → review
```

- **Источники.** Greenhouse, Lever, Ashby, Workable — публичные API досок, без логина, браузера и капчи. Фирмы без ATS
  (Alber Blanc, Wunder Fund, QST, SPECTRAL…) — `ats: site`: страница карьеры и ссылки на вакансии по шаблону
  `links`, либо слежение за изменениями всей страницы. Списки SimplifyJobs на GitHub (`watch.aggregators`) приносят
  quant-стажировки и позиции для выпускников из фирм, которых нет в `firms.yaml` (Workday, iCIMS — подача вручную).
  Доски в `firms.yaml` проверены на живых данных; заново — `quant-agent firms --validate`.
- **Тип вакансии**: `internship | graduate | full_time` по названию и полям ATS (Workflow «Campus», «Summer Internship»…).
  По умолчанию оцениваются только стажировки и позиции для выпускников (`filters.kinds`); у фирмы в `firms.yaml`
  можно задать свои `kinds` (например, брать и junior full-time у русскоязычных команд).
- **Условия допуска без LLM** (`eligibility`): «только для студентов вуза», годы выпуска («between May 2025 and
  February 2028», «graduating by 2027»), «no visa sponsorship», явный запрет на кандидатов из страны. Не подходит —
  `ineligible` с причиной, попытку в фирму не тратим. Сомнительные случаи LLM видит и решает при оценке.
- **Оценка LLM**: балл 0–100, трек (quant research / quant dev / ML infra), **уверенность по каждому измерению
  отдельно от балла**, до трёх вещей «что проверить», риски, блокер. Тексты вакансий передаются как данные, инструкции
  в них игнорируются. Местоположение кандидата — только из `eligibility.current_location`.
- **ATS-скор без LLM**: какие ключевые слова вакансии есть в резюме, а каких нет.
- **Закрытие**: вакансия пропала с доски — `closed`; вернулась — прежний статус. Ошибка доски не закрывает вакансии
  и видна в `stats` (здоровье источников).
- **Лимит заявок на фирму**: `max_applications` за `cooldown_months`; `review` предупреждает перед лишней заявкой.

## Установка и запуск

```bash
uv sync
cp config.example.yaml config.yaml      # заполнить eligibility, preferences, резюме
uv run quant-agent firms --validate     # доски отвечают?
uv run quant-agent watch                # опросить доски и оценить новые вакансии
uv run quant-agent review               # лучшие вакансии; [o] открыть форму, [a] отметить заявку
uv run quant-agent add <url>            # одна вакансия по ссылке (Greenhouse/Lever/Ashby/Workable или любая страница)
uv run quant-agent stats
```

## Автопилот (Greenhouse, Lever, Ashby, Workable)

```bash
uv run quant-agent autopilot              # один проход: опрос и оценка в фоне, формы подряд; режим из apply.mode
uv run quant-agent autopilot --mode dry_run         # формы заполняются без отправки
uv run quant-agent answers                # ответить на вопросы анкет, которых агент не знает
uv run quant-agent apply <uid> --mode assist         # одна вакансия
```

- **План ответов до браузера.** Поля формы: Greenhouse — API (`?questions=true`), Lever — HTML формы
  (`cards[…][baseTemplate]`), Ashby — GraphQL страницы формы, Workable — API формы (`/api/v1/jobs/<shortcode>/form`;
  Address Workable подставляет по IP — агент печатает поверх `profile.city`). Источники ответов:
  профиль (`profile`), резюме и письмо, банк ответов `answers.yaml`, вопросы с единственным вариантом-согласием
  (`apply.auto_consent`), остальное — LLM строго по резюме и фактам. Чего в фактах нет (GPA, местоимения, регион,
  другие офферы…) — статус `needs_answers`: отвечаете один раз командой `answers`, ответ подходит для такого же
  вопроса в формах других фирм.
- **Заполнение — как в hh-agent**: «чтение» вакансии, паузы между полями, печать посимвольно, блок образования из
  профиля. После заполнения каждое поле сверяется с планом; не сошлось — `manual` со снимком экрана, заявка не уходит.
- **Режимы** (`apply.mode`): `assist` — агент заполняет, Submit нажимаете вы (агент ждёт страницу подтверждения);
  `auto` — Submit жмёт агент, при reCAPTCHA с задачей — уведомление и ожидание вас; `dry_run` — только снимок.
- **Код из письма.** Greenhouse после Submit просит security code из письма. Агент видит поле кода, читает письмо
  по IMAP (секция `mail`), вписывает код и снова жмёт Submit — и в `assist`, и в `auto`. Для Gmail нужен пароль
  приложения (myaccount.google.com/apppasswords) в `.env`: `IMAP_PASSWORD=…`. Проверка: `uv run quant-agent mail`.
  Пароля нет или письмо не пришло за `mail.code_wait_seconds` — уведомление, код вводите вы.
- **Проход** — как autopilot в hh-agent: опрос досок, оценка и письма идут в фоне, а заявки подаются сразу из
  очереди, одна за другой с паузой `apply.delay_seconds`; после каждой очередь пересчитывается. Очередь пуста и
  оценка закончена — программа выходит. Повторять — запуском по расписанию (cron, launchd).
- **Лимиты**: `max_applications` за `cooldown_months` на фирму, `apply.daily_limit`,
  `apply.max_per_run`, рабочие часы. Подаёт только на вакансии с баллом ≥ `apply.min_score` и с письмом.

Workday и письма-алерты LinkedIn/eFC — следующие шаги. getmatch.ru — через Telegram-бота в hh-agent.

## Разработка

```bash
uv run ruff check .
uv run pytest
```

Тесты не ходят в сеть и не зовут LLM: ответы досок — в `tests/fixtures/` (сохранены с живых досок и обрезаны),
LLM подменяется. Личное (`config.yaml`, резюме, база, `.env`, `logs/`) — в `.gitignore`.
