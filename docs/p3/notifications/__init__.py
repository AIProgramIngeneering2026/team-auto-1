"""Сервис уведомлений — доменный слой практики 3: Job и Registry.

Правила переходов состояний Job (диаграмма состояний из практики 2) живут
внутри класса Job. Registry выдаёт Tool по имени операции; неизвестная
операция — явная ошибка UnknownOperationError (код ``unknown_operation``).
"""

from notifications.errors import (
    InvalidTransitionError,
    NotificationError,
    UnknownOperationError,
)
from notifications.job import Job, JobState
from notifications.registry import Registry

__all__ = [
    "Job",
    "JobState",
    "Registry",
    "InvalidTransitionError",
    "UnknownOperationError",
    "NotificationError",
]
