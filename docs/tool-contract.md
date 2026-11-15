# Контракт Tool

Tool — исполнитель одного канала доставки (email, Telegram, webhook). Dispatcher не знает конкретных Tool:
он берёт нужный через Registry по имени канала и вызывает `send()`. Новый канал добавляется новым классом и
регистрацией в Registry, без правок Dispatcher.

Нотация ниже языково-нейтральная; при реализации переносится в абстрактный класс, интерфейс или протокол
выбранного языка.

## 1. Интерфейсы

```text
interface Tool:
    channel: string                                  # "email" | "telegram" | "webhook"
    validate(params: SendParams) -> list<FieldError> # чистая функция, без сети и БД
    send(request: SendRequest)   -> ToolResult       # одна попытка отправки, обращается к адаптеру

interface Registry:
    register(tool: Tool)
    get(channel: string) -> Tool                     # неизвестный канал -> UnknownChannelError
    channels() -> list<string>
```

### Что Tool получает

```text
SendParams:                      # то, что пришло от клиента
    recipient: string            # формат зависит от канала, см. раздел 4
    subject:   string | null     # используется только email
    body:      string            # итоговый текст: подстановку персональных данных уже сделал рендерер

SendRequest:                     # то, что Dispatcher передаёт в send()
    notification_id: string      # id Job; провайдеру передаётся как ключ дедупликации, где он поддерживается
    attempt:         int         # номер попытки, 1..3 (для логов, поведение Tool от него не зависит)
    params:          SendParams
```

### Что Tool возвращает

`send()` возвращает `ToolResult` и **не бросает исключений для ожидаемых отказов**.

```text
ToolResult = Success | Failure

Success:
    provider_message_id: string | null   # id сообщения у провайдера, если он его вернул

Failure:
    error: ToolError

ToolError:
    code:                string          # код из раздела 2
    kind:                "transient" | "permanent"
    message:             string          # для логов и поля error у Job, без секретов и без текста сообщения
    retry_after_seconds: int | null      # только для rate_limited, если провайдер сообщил паузу
```

```text
FieldError:                              # результат validate()
    field:   string                      # "recipient" | "subject" | "body"
    code:    string                      # "required" | "invalid_format" | "too_long"
    message: string
```

## 2. Ошибки, которыми Tool отказывает

Ошибка либо **временная** (`transient`), тогда Dispatcher повторяет отправку, всего до 3 попыток, либо
**постоянная** (`permanent`), тогда Job сразу переходит в «не доставлено» без повторов (сценарий С5).

| Код | kind | Когда | Что делает Dispatcher |
|---|---|---|---|
| `provider_timeout` | transient | канал не ответил за таймаут | повтор |
| `provider_unavailable` | transient | сеть недоступна, ответ провайдера 5xx | повтор |
| `rate_limited` | transient | провайдер ответил «слишком часто» (429); `retry_after_seconds` — подсказка паузы | повтор не раньше паузы |
| `bad_provider_response` | transient | ответ провайдера нельзя разобрать; это **неудачная попытка, а не доставка** | повтор |
| `recipient_rejected` | permanent | адрес не существует, бот заблокирован, чат не найден | сразу «не доставлено» |
| `invalid_params` | permanent | параметры не прошли `validate()` внутри `send()` | сразу «не доставлено» |

Значение `code` последней неудачной попытки записывается в поле `error` Job и отдаётся клиенту через
`GET /notifications/{id}` (сценарий С2).

Если `send()` всё же бросил исключение (баг в Tool), Dispatcher ловит его, считает попытку неудачной
временной ошибкой с кодом `internal_error` и пишет её в лог. Это защита от багов, а не часть контракта Tool.

## 3. Кто проверяет параметры

Правила проверки знает **Tool**, а **вызывающий обязан их применить** до отправки.

| Кто | Когда | Что делает |
|---|---|---|
| Входной слой (API) | при `POST /notifications`, до создания Job | `registry.get(channel)`: неизвестный канал даёт `400 unknown_channel`; затем `tool.validate(params)`: непустой список ошибок даёт `400 invalid_params`, `details.field` берётся из первой ошибки. Job не создаётся |
| Dispatcher | перед отправкой | повторно **не** проверяет: в БД лежат уже проверенные данные |
| Tool.send | в начале каждой попытки | вызывает собственный `validate()` (он дешёвый); при ошибках возвращает `Failure(invalid_params, permanent)` |

Зачем двойная проверка: Tool можно вызвать не только из входного слоя (тесты, повторный запуск, во втором семестре
выбор Tool и параметров будет делать LLM, а её вывод считается недоверенным). Tool не должен полагаться на то, что
до него «кто-то уже проверил».

## 4. Общие правила для любого Tool

1. **Без состояния и без хранилища.** Tool не читает и не пишет БД, не меняет состояние Job, не знает про Job.
2. **Одна попытка за вызов.** Повторами управляет Dispatcher; скрытых повторов внутри Tool нет.
3. **Обязательный таймаут** обращения к адаптеру (значение из конфигурации, по умолчанию 10 с). Превышение даёт `provider_timeout`.
4. **Единый вид ответа.** Любая ошибка провайдера отображается в код из раздела 2; сырые тексты провайдера наружу не уходят.
5. **Не логирует** текст сообщения, токены и ключи.
6. **Работает через адаптер внешней зависимости** (в v1 это тестовая заглушка). Адаптер передаётся в конструктор, поэтому тесты подменяют его без сети.
7. `notification_id` передаётся провайдеру как ключ дедупликации там, где это возможно. Гарантия доставки остаётся «не меньше одного раза» (см. ADR-0001).

## 5. Описание Tool через контракт

### Tool 1. EmailTool (`channel = "email"`)

| Параметр | Правила `validate()` |
|---|---|
| `recipient` | обязателен; адрес вида `локальная@домен.зона`, до 254 символов, иначе `invalid_format` |
| `subject` | обязателен, 1–200 символов |
| `body` | обязателен, 1–10 000 символов |

Отображение ответов провайдера в ошибки:

| Ситуация у провайдера | Результат |
|---|---|
| письмо принято | `Success(provider_message_id)` |
| нет ответа за таймаут | `Failure(provider_timeout, transient)` |
| временный отказ, 4xx SMTP или 5xx API | `Failure(provider_unavailable, transient)` |
| ящик не существует (SMTP 550) | `Failure(recipient_rejected, permanent)` |
| ответ не разобрать | `Failure(bad_provider_response, transient)` |

### Tool 2. TelegramTool (`channel = "telegram"`)

| Параметр | Правила `validate()` |
|---|---|
| `recipient` | обязателен; числовой `chat_id` (цифры, возможен ведущий минус), иначе `invalid_format` |
| `subject` | не используется; если передан, игнорируется |
| `body` | обязателен, 1–4096 символов (лимит Telegram), иначе `too_long` |

| Ситуация у провайдера | Результат |
|---|---|
| сообщение принято | `Success(provider_message_id)` |
| нет ответа за таймаут | `Failure(provider_timeout, transient)` |
| 429 «слишком часто» | `Failure(rate_limited, transient, retry_after_seconds)` |
| 5xx или сеть недоступна | `Failure(provider_unavailable, transient)` |
| 403 бот заблокирован пользователем, 400 чат не найден | `Failure(recipient_rejected, permanent)` |
| ответ не разобрать | `Failure(bad_provider_response, transient)` |

### Tool 3. WebhookTool (`channel = "webhook"`)

| Параметр | Правила `validate()` |
|---|---|
| `recipient` | обязателен; URL со схемой `https`, до 2048 символов |
| `subject` | не используется |
| `body` | обязателен, 1–65 536 символов |

Ответ 2xx означает `Success`; 5xx и сеть дают `provider_unavailable`; таймаут даёт `provider_timeout`;
ответ 4xx, кроме 429, означает `recipient_rejected` (endpoint отверг запрос, повтор бессмыслен); 429 даёт `rate_limited`.

## 6. Контрактные тесты (для любого Tool)

Один параметризованный набор, запускаемый для каждой реализации с заглушкой-адаптером:

- валидные параметры и заглушка «ок» дают `Success`;
- каждая строка `validate()` (пустой получатель, неверный формат, слишком длинный текст) даёт непустой список `FieldError` с нужным `field`;
- `send()` с невалидными параметрами даёт `Failure(invalid_params, permanent)`, адаптер не вызывается;
- заглушка «таймаут» даёт `provider_timeout`, заглушка «мусорный ответ» даёт `bad_provider_response`, оба `transient`;
- заглушка «адрес не существует» или «бот заблокирован» даёт `recipient_rejected`, `permanent`;
- `send()` не бросает исключений ни в одном из перечисленных случаев.
