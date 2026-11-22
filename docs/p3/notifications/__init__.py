"""Сервис уведомлений — доменный слой практики 3: Job и Registry.

Правила переходов состояний Job (диаграмма состояний из практики 2) живут
внутри класса Job. Registry выдаёт Tool по имени операции; неизвестная
операция — явная ошибка UnknownOperationError (код ``unknown_operation``).
Хранение — через интерфейс (``store``), реализация в памяти.
"""

from notifications.errors import (
    InvalidTransitionError,
    JobNotFoundError,
    NotificationError,
    UnknownOperationError,
)
from notifications.job import Job, JobState
from notifications.registry import Registry
from notifications.store import InMemoryJobStore

__all__ = [
    "Job",
    "JobState",
    "Registry",
    "InMemoryJobStore",
    "InvalidTransitionError",
    "UnknownOperationError",
    "JobNotFoundError",
    "NotificationError",
]
