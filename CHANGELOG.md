# Changelog

Все заметные изменения quant-job-agent. Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
версии — [SemVer](https://semver.org/lang/ru/); собирается [git-cliff](https://git-cliff.org) из коммитов.

## [Не выпущено]

### 🚀 Новое

- **log:** Журнал как в hh-agent ([`fc89956`](https://github.com/dimicorn/quant-job-agent/commit/fc899569b0baebb0db321c874fffb0f9dad453e7))
- **sources:** Workable — доска, форма и автоотклик; Pinely переехала на Workable ([`57fbdc3`](https://github.com/dimicorn/quant-job-agent/commit/57fbdc394b90389c86dcf2032f47aabf1101efcc))
- **firms:** Добавить Graviton и iSAM (доски Greenhouse) ([`749fb95`](https://github.com/dimicorn/quant-job-agent/commit/749fb95f9b1f2b31c0b0187f37a7b7c04d4dc90f))
- **autopilot:** ⚠️ **несовместимо** — Один проход за запуск, как в hh-agent ([`34aeec4`](https://github.com/dimicorn/quant-job-agent/commit/34aeec494aefde1373817065c51d36937fe0a341))
- **firms:** Добавить Marshall Wace (доска graduate-программ mw-tech-grad) ([`61d6dd1`](https://github.com/dimicorn/quant-job-agent/commit/61d6dd162be9233d865f5d410040e12d768684f1))
- **mail:** Вводить код подтверждения из письма после Submit ([`72c8f03`](https://github.com/dimicorn/quant-job-agent/commit/72c8f03c3f224f4f2584285f7bf5fb1e35300e86))
- Агент откликов на стажировки и вакансии в квант-фирмах Европы и UK ([`aba20d9`](https://github.com/dimicorn/quant-job-agent/commit/aba20d9b28979215e49961a5b47a8695c3c57610))

### 🐛 Исправления

- Баги из проверки проекта — черновики Workable, YAML-ответы, сбои LLM, сети и досок ([`1b7c6b0`](https://github.com/dimicorn/quant-job-agent/commit/1b7c6b0b0d88c09d1f08896f81efc55952ad5489))
- **apply:** Выпадающий список Workable, формы manual остаются вам, Chrome вместо Chrome for Testing ([`ce0e5dc`](https://github.com/dimicorn/quant-job-agent/commit/ce0e5dc53b2ea8143120f418fc8bb33f1405e4ed))
- Переживать мигание сети и формы, удалённые с сайта ([`9237216`](https://github.com/dimicorn/quant-job-agent/commit/9237216bcb0562834f6280654c7691199becfed9))
- Баги из проверки проекта ([`8856354`](https://github.com/dimicorn/quant-job-agent/commit/88563542fc9130b15f7c4b92496b2ed79829098b))
- **answers:** Ответ на вопрос с другими вариантами — отдельно для фирмы ([`1278216`](https://github.com/dimicorn/quant-job-agent/commit/1278216c63240c36ced1beb97f32a153551574e9))
- **mail:** Не падать на письмах с битой кодировкой заголовков ([`6e8f059`](https://github.com/dimicorn/quant-job-agent/commit/6e8f0594b68ce7b124960b0f9624761b0a9b2562))
- **apply:** Сокращать ответ под maxlength и переживать закрытое окно браузера ([`a3bf510`](https://github.com/dimicorn/quant-job-agent/commit/a3bf510cb180c8719aa686ce0cb44db248b18c3e))

