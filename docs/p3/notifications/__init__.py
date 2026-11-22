"""Сервис уведомлений — доменный слой практики 3: Job и Registry.

Правила переходов состояний Job (диаграмма состояний из практики 2) живут
внутри класса Job. Registry выдаёт Tool по имени операции; неизвестная
операция — явная ошибка UnknownOperationError (код ``unknown_operation``).
"""

from notifications.dispatcher import Dispatcher, RetryPolicy
from notifications.errors import (
    InvalidParamsError,
    InvalidTransitionError,
    JobNotFoundError,
    NotificationError,
    UnknownOperationError,
)
from notifications.job import Job, JobState
from notifications.registry import Registry
from notifications.repository import InMemoryJobRepository, JobRepository

__all__ = [
    "Job",
    "JobState",
    "Registry",
    "Dispatcher",
    "RetryPolicy",
    "JobRepository",
    "InMemoryJobRepository",
    "InvalidTransitionError",
    "InvalidParamsError",
    "UnknownOperationError",
    "JobNotFoundError",
    "NotificationError",
]
