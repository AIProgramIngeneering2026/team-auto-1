"""Хранилище Job: интерфейс и реализация в памяти.

Ядру нужно хранение, но по заданию оно не должно зависеть от БД, поэтому
Dispatcher работает с интерфейсом ``JobRepository``. Сейчас достаточно
реализации в памяти; на практике 4 появится реализация поверх PostgreSQL
(ADR-0001: те же операции, переход «принято -> отправляется» и отмена —
условным ``UPDATE ... WHERE status = 'accepted'``).

Главное требование к хранилищу — АТОМАРНОСТЬ двух операций, которые
конкурируют за один Job в состоянии «принято»:

* ``start_sending`` — Dispatcher берёт Job в работу;
* ``cancel``        — клиент отменяет Job.

Выполняется ровно одна из них (сценарий С3, «гонка отмены и запуска»).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from notifications.errors import InvalidTransitionError, JobNotFoundError
from notifications.job import Job, JobState


class JobRepository(ABC):
    """Что ядро требует от хранилища."""

    @abstractmethod
    def add(self, job: Job) -> None:
        """Сохранить новый Job. Повторный ``id`` — ValueError."""

    @abstractmethod
    def get(self, job_id: str) -> Job:
        """Найти Job. Нет такого — JobNotFoundError."""

    @abstractmethod
    def start_sending(self, job_id: str) -> Job | None:
        """Атомарно: accepted -> sending (attempts = 1).

        Вернуть Job, если переход выполнен; ``None``, если Job уже не в
        ``accepted`` (его отменили или взял другой воркер) — тогда
        отправлять нечего. Нет такого Job — JobNotFoundError.
        """

    @abstractmethod
    def cancel(self, job_id: str) -> Job:
        """Атомарно: accepted -> cancelled.

        Не из ``accepted`` — InvalidTransitionError (в API это 409
        ``cannot_cancel``). Нет такого Job — JobNotFoundError.
        """

    @abstractmethod
    def save(self, job: Job) -> None:
        """Сохранить изменённое состояние Job (attempts, error, state).

        Dispatcher вызывает после каждого перехода, который сам сделал
        (повтор, доставлено, не доставлено, interrupted). Реализация поверх
        БД обязана записать их: без этого изменения остались бы только в
        памяти. Нет такого Job — JobNotFoundError.
        """

    @abstractmethod
    def find_by_state(self, state: JobState) -> list[Job]:
        """Все Job в заданном состоянии (нужно при перезапуске сервиса)."""


class InMemoryJobRepository(JobRepository):
    """Хранилище в памяти. Все операции под одним замком (потокобезопасно)."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> None:
        with self._lock:
            if job.id in self._jobs:
                raise ValueError(f"Job с id {job.id!r} уже существует")
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Job:
        with self._lock:
            return self._get_locked(job_id)

    def start_sending(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._get_locked(job_id)
            try:
                job.start_sending()
            except InvalidTransitionError:
                return None  # отменён или уже взят — проигравший пропускает Job
            return job

    def cancel(self, job_id: str) -> Job:
        with self._lock:
            job = self._get_locked(job_id)
            job.cancel()  # не из accepted -> InvalidTransitionError
            return job

    def save(self, job: Job) -> None:
        with self._lock:
            self._get_locked(job.id)  # нет такого Job — JobNotFoundError
            self._jobs[job.id] = job

    def find_by_state(self, state: JobState) -> list[Job]:
        with self._lock:
            return [job for job in self._jobs.values() if job.state is state]

    def _get_locked(self, job_id: str) -> Job:
        try:
            return self._jobs[job_id]
        except KeyError:
            raise JobNotFoundError(job_id) from None
