"""Явные ошибки домена.

Ошибки домена — это не «молчаливое игнорирование» и не исключение
«на всякий случай»: каждая ошибка имеет стабильный машинный код ``code``
и человекочитаемое сообщение. Вызывающий код (входной слой, Dispatcher)
переводит их в HTTP-коды контракта (409 cannot_cancel и т.п.).
"""

from __future__ import annotations


class NotificationError(Exception):
    """Базовый класс явных ошибок домена сервиса уведомлений."""

    code: str = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidTransitionError(NotificationError):
    """Недопустимый переход состояния Job.

    Возникает в самом Job в момент попытки перехода, которого нет в таблице
    разрешённых переходов (диаграмма состояний из практики 2).
    Состояние Job при этом не меняется.
    """

    code = "invalid_transition"

    def __init__(self, from_state: str, to_state: str, event: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        self.event = event
        super().__init__(
            f"недопустимый переход состояния Job: {from_state} -> {to_state} "
            f"(событие '{event}')"
        )


class UnknownOperationError(NotificationError):
    """Неизвестная операция (канал), запрошенная у Registry.

    Registry.get(name) не нашёл Tool с таким именем. Код ошибки наш
    собственный: ``unknown_operation``.
    """

    code = "unknown_operation"

    def __init__(self, operation: str, known: list[str] | None = None) -> None:
        self.operation = operation
        self.known = list(known) if known else []
        hint = f", известные операции: {', '.join(self.known)}" if self.known else ""
        super().__init__(f"неизвестная операция: {operation!r}{hint}")


class JobNotFoundError(NotificationError):
    """Job с таким id не существует (сценарий С2: 404 notification_not_found)."""

    code = "notification_not_found"

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"уведомление не найдено: {job_id!r}")
