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

```bash
python3 -m pytest tests/ -v   # 35 тестов
python3 examples/demo.py      # демонстрация заданий 1 и 2
```

## Структура

```
notifications/
├── errors.py          # InvalidTransitionError, UnknownOperationError
├── job.py             # Job, JobState, таблица переходов _TRANSITIONS
├── registry.py        # Registry: register / get / channels
└── tools/
    ├── base.py        # Tool (ABC), SendParams, SendRequest, ToolResult, адаптер
    ├── email.py       # EmailTool: validate() по tool-contract.md (Tool 1)
    ├── telegram.py    # TelegramTool (Tool 2)
    ├── webhook.py     # WebhookTool (Tool 3)
    └── stub_adapter.py  # заглушка провайдера (v1): ok / timeout /
                          # unavailable / rate_limited / bad_response / rejected;
                          # неизвестный сценарий — ошибка конфигурации
tests/
├── test_job_transitions.py   # каждая стрелка и каждый запрет диаграммы
├── test_registry.py           # выдача Tool, unknown_operation, регистрация
└── test_review_fixes.py       # сценарии заглушки, rehydrate, пустой error
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
через `Job(...)` нельзя. Единственная точка, где состояние задаётся напрямую, —
`Job.rehydrate(...)` (восстановление сохранённого Job из хранилища для
`GET /notifications/{id}`); она проверяет снимок (валидный `JobState`,
неотрицательный `attempts`, обязательная причина для `undelivered`), а
восстановленный объект дальше живёт по тем же правилам переходов.
`mark_undelivered` отклоняет пустую причину: клиент должен видеть,
почему не доставлено (сценарии С2/С5).

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
| Перезапуск: sending → undelivered, `error = interrupted` | `Job.interrupt()` |
| Registry: register / get / channels (`tool-contract.md` §1) | `registry.py` |
| Неизвестный канал/операция — явная ошибка (`contract-check.md` К3) | `UnknownOperationError`, код `unknown_operation` |
| validate() трёх Tool (`tool-contract.md` §5) | `tools/email.py`, `telegram.py`, `webhook.py` |
| Заглушка провайдера вместо сети (v1) | `tools/stub_adapter.py` |

Что сознательно не входит (объём практики — «Job + Registry»): Dispatcher с
политикой повторов и классификацией transient/permanent, персистентность в
PostgreSQL, HTTP-слой, идемпотентность по ключу — это следующие шаги, контракт
`Job` и `Registry` под них уже рассчитан.
