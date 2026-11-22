# Практика 3: Job с проверкой переходов + Registry

Продолжение практики по сервису уведомлений (файлы проекта: `requirements.md`,
`architecture.md`, `tool-contract.md`, `contract-check.md`, `backlog.md`).
Реализованы два задания:

1. **Job с проверкой переходов** — по диаграмме состояний из практики 2
   (`architecture.md`, раздел 3). Недопустимый переход отклоняется **явной
   ошибкой** `InvalidTransitionError`, а не игнорируется молча. Правило живёт
   в `Job`, а не в вызывающем коде.
2. **Registry** — по имени операции (канала) выдаёт Tool. Неизвестная операция —
   ошибка «неизвестная операция»: `UnknownOperationError`, собственный код
   ошибки — `unknown_operation`.

Язык — Python 3.11+ (без внешних зависимостей, кроме pytest для тестов).
Хранение — память (по решению на этой практике).

## Запуск

Одна команда на все тесты (101 тест):

```bash
python3 -m pytest tests/ -v
python3 examples/demo.py      # демонстрация заданий 1 и 2
```

Обязательные покрытия задания: обычный путь Job — `test_happy_path.py`;
злодейства З1–З6 — `test_evil_deeds.py`; неизвестная операция —
`test_registry.py`; неверные параметры — `test_tool_params.py`.

## Структура

```
notifications/
├── errors.py          # InvalidTransitionError, UnknownOperationError,
│                      # JobNotFoundError
├── job.py             # Job, JobState, таблица переходов _TRANSITIONS,
│                      # инварианты снимков (_rehydrate), лимит попыток
├── registry.py        # Registry: register / get / channels
├── store.py           # InMemoryJobStore: один Job на id, условное восстановление
└── tools/
    ├── base.py        # Tool (ABC), SendParams, SendRequest, ToolResult, адаптер
    ├── email.py       # EmailTool: validate() по tool-contract.md (Tool 1)
    ├── telegram.py    # TelegramTool (Tool 2)
    ├── webhook.py     # WebhookTool (Tool 3)
    └── stub_adapter.py  # заглушка провайдера (v1): ok / timeout /
                          # unavailable / rate_limited / bad_response / rejected;
                          # неизвестный сценарий — ошибка конфигурации
tests/
├── test_happy_path.py       # обычный путь: создание -> sending -> delivered
├── test_evil_deeds.py        # злодейства З1–З6: каждое — отдельный тест
├── test_tool_params.py       # неверные параметры с указанием поля (3 Tool)
├── test_job_transitions.py   # каждая стрелка и каждый запрет диаграммы
├── test_registry.py           # выдача Tool, unknown_operation, регистрация
└── test_review_fixes.py       # сценарии заглушки, инварианты restore, лимиты
examples/demo.py
```

## Как правило переходов живёт в Job

Состояние Job (`accepted / sending / delivered / undelivered / cancelled`)
меняется **только** методами-событиями:

| Метод | Событие | Переход |
| --- | --- | --- |
| `start_sending()` | start | accepted → sending (attempts = 1) |
| `record_retry()` | retry | sending → sending (attempts + 1) |
| `mark_delivered(id)` | deliver | sending → delivered |
| `mark_undelivered(error)` | fail | sending → undelivered (причина в `error`) |
| `interrupt()` | interrupt | sending → undelivered (`error = interrupted`, перезапуск) |
| `cancel()` | cancel | accepted → cancelled |

Таблица `_TRANSITIONS` в `job.py` — единственный источник истины: событие
допустимо только из перечисленных в нём состояний; иначе `Job._apply()`
бросает `InvalidTransitionError` с полями `from_state`, `to_state`, `event`
и кодом `invalid_transition`. Состояние, `attempts` и `error` при этом
не меняются. Обойти правило нельзя: у `state` нет сеттера (`@property` без
setter — попытка присваивания даёт `AttributeError`), а `_apply` приватный.
Сеттеров нет и у `id / channel / recipient / text` — Job неизменяем после
создания (таблица запрещённых переходов в `architecture.md`).

Вызывающий код не решает, какой переход разрешён: он вызывает событие и либо
ловит `InvalidTransitionError`, либо видит новое состояние. Соответствие
HTTP-контракту (см. `architecture.md`): `cancel()` из не-`accepted` →
409 `cannot_cancel`; терминальные состояния — никаких операций изменения.

Новый Job создаётся конструктором только в `accepted`; подсунуть состояние
через `Job(...)` нельзя. Восстановление сохранённого Job — только через слой
хранения `InMemoryJobStore.restore()` (→ внутренний `Job._rehydrate`):
- снимок обязан быть физически возможной историей — успех ⇒ была попытка,
  `sending`/`undelivered` ⇒ 1..3 попытки, `error` только у `undelivered`
  и только из допустимых кодов (FailureReason), `cancelled` ⇒ попыток нет;
- для уже известного id допустим только идентичный снимок; иное —
  `InvalidTransitionError` (условная запись, как conditional UPDATE в ADR-0001).
Один id — один живой объект Job, «параллельной копии с готовым состоянием»
через хранилище не существует.
`mark_undelivered` отклоняет пустую причину: клиент должен видеть,
почему не доставлено (сценарии С2/С5).

## Злодейства З1–З6: отчёт

Атака (сыгран атакующий) нашла дыру: старый `Job.rehydrate()` принимал
любой согласованный снапшот и позволял воскрешать терминальные Job. Дыра
закрыта двумя уровнями — инварианты снимков в `Job._rehydrate` и условное
`restore()` в хранилище. Ниже — применимость каждого злодейства; все цепочки
проверены тестами `test_evil_deeds.py`.

| Злодейство | Что делает ядро | Где закрыто | Тест |
| --- | --- | --- | --- |
| З1 двойная работа | Job не знает исполнителей — на уровне Job неприменимо (объяснение: исполнителей видит только Dispatcher, «один работник на Job» — его зона); ядро ограничивает последствия: попыток не больше 3, повтор сверх лимита — явная ошибка | `Job.record_retry` (лимит MAX_ATTEMPTS) | `test_evil_z1_double_work` |
| З2 вечная работа | восстановленный `sending` при старте прерывается в `undelivered`/`interrupted`; даже без прерывания работа не вечная — попытки кончаются | `Job.interrupt()`, `store.jobs_in_state()`, лимит попыток | `test_evil_z2_eternal_sending` |
| З3 отменённое стало готовым | `restore()` для известного id с другим состоянием — `InvalidTransitionError`; Job остаётся `cancelled` | `store.restore` (условная запись) | `test_evil_z3_cancelled_becomes_delivered` |
| З4 два итога | смена `undelivered` → `delivered` через снимок — тот же запрет, итог неизменен | `store.restore` | `test_evil_z4_undelivered_becomes_delivered` |
| З5 успех без работы | `delivered` с нулём попыток — невозможная история, снимок отклоняется | инвариант `_rehydrate` (delivered ⇒ attempts ≥ 1) | `test_evil_z5_success_without_work` |
| З6 повтор успешного | возврат `delivered` → `sending` невозможен ни методом, ни снимком — только новый Job (К5) | `store.restore` + таблица переходов | `test_evil_z6_delivered_sent_again` |
| З0 сломанный обычный путь | неприменимо сейчас: «только в части 3»; обычный путь после починки покрыт и проходит | `test_happy_path.py` | `test_evil_z0_normal_path_still_works` |

## Как Registry выдаёт Tool

`registry.get(operation)` возвращает Tool, зарегистрированный под именем
канала (`email`, `telegram`, `webhook`). Неизвестная операция (`sms2`,
пустая строка, имя в другом регистре) — `UnknownOperationError`:

- `code = "unknown_operation"` — собственный код, как решено на практике;
- сообщение называет операцию и подсказывает известные каналы.

Регистрация повторно одним и тем же объектом идемпотентна; занятый канал —
`ValueError` (ошибка конфигурации). Новый канал добавляется новым классом
Tool и `registry.register(...)` — Dispatcher не меняется (контракт Tool,
раздел 1). В v1 все Tool работают через `StubAdapter` — реальных сетевых
вызовов нет (`architecture.md`, «Адаптер внешней зависимости»).

## Связь с документами проекта

| Требование | Где реализовано |
| --- | --- |
| Жизненный цикл Job, пять состояний (`requirements.md`) | `job.py`: `JobState` |
| Разрешённые/запрещённые переходы, 409-семантика (`requirements.md`, `architecture.md` §3) | `job.py`: `_TRANSITIONS` + `InvalidTransitionError` |
| Перезапуск: sending → undelivered, `error = interrupted` | `Job.interrupt()` + `store.jobs_in_state()` |
| До 3 попыток (С5) | `job.py`: `MAX_ATTEMPTS`, лимит в `record_retry` |
| Registry: register / get / channels (`tool-contract.md` §1) | `registry.py` |
| Неизвестный канал/операция — явная ошибка (`contract-check.md` К3) | `UnknownOperationError`, код `unknown_operation` |
| validate() трёх Tool (`tool-contract.md` §5) | `tools/email.py`, `telegram.py`, `webhook.py` |
| Ядро без фреймворка и БД; хранение через интерфейс, реализация в памяти | только stdlib; `store.py`: `InMemoryJobStore` |
| Несуществующий id — 404 `notification_not_found` (С2) | `JobNotFoundError` в `errors.py` |
| Заглушка провайдера вместо сети (v1) | `tools/stub_adapter.py` |

Что сознательно не входит (объём практики — «Job + Registry»): Dispatcher с
политикой повторов и классификацией transient/permanent, персистентность в
PostgreSQL, HTTP-слой, идемпотентность по ключу — это следующие шаги, контракт
`Job` и `Registry` под них уже рассчитан.
