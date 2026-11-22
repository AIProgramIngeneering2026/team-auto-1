"""Типы контракта Tool (tool-contract.md, раздел 1) и интерфейсы.

Нотация контракта перенесена в абстрактный класс ``Tool`` и датаклассы.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

# ---------------------------------------------------------------- Типы


@dataclass(frozen=True)
class SendParams:
    """То, что пришло от клиента (после валидации входного слоя)."""

    recipient: str
    text: str
    subject: str | None = None  # только для email; из HTTP API v1 приходит null


@dataclass(frozen=True)
class SendRequest:
    """То, что Dispatcher передаёт в send()."""

    notification_id: str
    attempt: int  # 1..3, для логов; поведение Tool от него не зависит
    params: SendParams


@dataclass(frozen=True)
class Success:
    """Канал подтвердил приём."""

    provider_message_id: str | None = None


@dataclass(frozen=True)
class Failure:
    """Ожидаемый отказ канала — не исключение."""

    error: "ToolError"


@dataclass(frozen=True)
class ToolError:
    """Ошибка Tool: стабильный код, класс временная/постоянная, сообщение."""

    code: str  # provider_timeout | provider_unavailable | rate_limited | ...
    kind: str  # "transient" | "permanent"
    message: str
    retry_after_seconds: int | None = None  # только для rate_limited


@dataclass(frozen=True)
class FieldError:
    """Результат validate(): неверное поле и почему."""

    field: str  # "recipient" | "subject" | "text"
    code: str  # "required" | "invalid_format" | "too_long"
    message: str


ToolResult = Success | Failure


# ---------------------------------------------------------------- Адаптер

class ProviderAdapter(ABC):
    """Адаптер внешней зависимости: обращение к провайдеру.

    В v1 реализация — тестовая заглушка (StubAdapter): реальных сетевых
    вызовов нет. Подмена адаптера в конструкторе Tool делает каналы
    проверяемыми без сети (правило 6 контракта Tool).
    """

    @abstractmethod
    def deliver(self, channel: str, request: SendRequest) -> ToolResult:
        """Одно обращение к провайдеру; ответ уже в виде ToolResult."""
        raise NotImplementedError


# ---------------------------------------------------------------- Tool


class Tool(ABC):
    """Исполнитель одного канала доставки.

    Без состояния и без хранилища; одной попытке — один send();
    валидация — чистая функция; ошибки канала возвращаются ToolResult'ом,
    ожидаемые отказы не бросают исключений (раздел 4 контракта).
    """

    #: имя канала ("email" | "telegram" | "webhook") — задаётся подклассом
    channel: str

    @abstractmethod
    def validate(self, params: SendParams) -> list[FieldError]:
        """Чистая функция без сети и БД: список ошибок, пустой = ок."""

    @abstractmethod
    def send(self, request: SendRequest) -> ToolResult:
        """Одна попытка отправки через адаптер."""
