"""Dispatcher — ведёт Job по состояниям и превращает ответ Tool в переход Job.

Что делает (architecture.md §2, tool-contract.md §2–3, ADR-0001):

1. Атомарно берёт Job в работу: accepted -> sending (хранилище решает гонку
   с отменой). Если Job уже не «принято» — пропускает его.
2. Через Registry получает Tool по имени канала и вызывает ``send()``.
   КОНКРЕТНЫХ Tool Dispatcher не знает: он видит только интерфейс Tool и
   результат Success/Failure. Новый канал = новый класс Tool + регистрация
   в Registry, правок здесь не нужно.
3. Превращает результат в переход Job и сохраняет его в хранилище:

   | ответ Tool                                  | что делает Dispatcher                     |
   |---------------------------------------------|-------------------------------------------|
   | Success                                     | sending -> delivered                      |
   | Failure transient, попытки остались         | пауза, sending -> sending (attempts + 1)  |
   | Failure transient, попытки исчерпаны        | sending -> undelivered, error = код       |
   | Failure transient, пауза провайдера больше  | не ждём, sending -> undelivered, error =  |
   | max_retry_after                             | код (например, rate_limited)              |
   | Failure permanent                           | сразу sending -> undelivered, без повторов|
   | исключение из send() / ответ не Success и   | попытка неудачна, временная ошибка        |
   | не Failure / Failure неверной формы         | ``internal_error``                        |
   | (баг в Tool)                                |                                           |
   | канала нет в Registry (ошибка настройки)    | сразу undelivered, ``internal_error``     |
   | любое иное исключение внутри Dispatcher     | Job из sending -> undelivered,            |
   |                                             | ``internal_error`` (Job не застревает)    |

4. Параметры НЕ проверяет: в хранилище лежат уже проверенные данные,
   а защитная проверка живёт в ``Tool.send`` (tool-contract.md §3).
   Если Tool всё же вернул ``invalid_params`` — это постоянная ошибка.

Гарантия доставки — «не меньше одного раза» (ADR-0001): если процесс упал
после фактической отправки, но до записи «доставлено», Job останется в
``sending``; при следующем старте ``recover_interrupted()`` переведёт его
в «не доставлено» с ``error = interrupted``.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Callable

from notifications.errors import UnknownOperationError
from notifications.job import Job, JobState
from notifications.registry import Registry
from notifications.repository import JobRepository
from notifications.tools.base import (
    TOOL_ERROR_CODES,
    Failure,
    SendParams,
    SendRequest,
    Success,
    ToolError,
    ToolResult,
)

logger = logging.getLogger(__name__)

#: Всего попыток на один Job (requirements.md, С5).
MAX_ATTEMPTS = 3

#: Дольше этого (секунд) просьбе провайдера «подождите» не подчиняемся:
#: воркер не должен засыпать на часы из-за одного ответа 429.
MAX_RETRY_AFTER = 60.0

_ERROR_KINDS = frozenset({"transient", "permanent"})


class RetryPolicy:
    """Сколько раз повторять и сколько ждать между попытками.

    Пауза — экспоненциальная с небольшим случайным разбросом (jitter), чтобы
    после сбоя провайдера все Job не ударили по нему одновременно
    (contract-check.md, находка №2, «шторм повторных попыток»):

        пауза = min(base_delay * 2^(n-1), max_delay) * (1 + случайное[0..jitter])

    где ``n`` — номер только что неудавшейся попытки. Если провайдер сам
    назвал паузу (``retry_after_seconds``, ответ 429), ждём не меньше неё —
    но не больше ``max_retry_after``: если провайдер просит подождать
    дольше, повтор бессмыслен для воркера, и Dispatcher не повторяет
    (см. ``accepts_pause``).
    """

    def __init__(
        self,
        max_attempts: int = MAX_ATTEMPTS,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: float = 0.2,
        max_retry_after: float = MAX_RETRY_AFTER,
        rng: random.Random | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError(f"max_attempts должно быть >= 1: {max_attempts}")
        if base_delay < 0 or max_delay < 0 or jitter < 0 or max_retry_after < 0:
            raise ValueError(
                "base_delay, max_delay, jitter и max_retry_after не могут быть отрицательными"
            )
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.max_retry_after = max_retry_after
        self._rng = rng or random.Random()

    def accepts_pause(self, retry_after_seconds: int | float | None) -> bool:
        """Можно ли выдержать паузу, которую назвал провайдер."""
        return retry_after_seconds is None or retry_after_seconds <= self.max_retry_after

    def delay(self, failed_attempt: int, retry_after_seconds: int | None = None) -> float:
        """Пауза (в секундах) перед следующей попыткой."""
        backoff = min(self.base_delay * 2 ** (failed_attempt - 1), self.max_delay)
        backoff *= 1 + self._rng.uniform(0, self.jitter)
        if retry_after_seconds is not None:
            return max(backoff, float(retry_after_seconds))
        return backoff


class Dispatcher:
    """Доставляет Job через Tool из Registry; состояние хранит JobRepository."""

    def __init__(
        self,
        registry: Registry,
        repository: JobRepository,
        retry_policy: RetryPolicy | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._policy = retry_policy or RetryPolicy()
        self._sleep = sleep  # подменяется в тестах, чтобы не ждать по-настоящему

    # ------------------------------------------------------------ публичное

    def dispatch(self, job_id: str) -> Job:
        """Выполнить отправку Job до конечного состояния и вернуть его.

        Нет такого Job — JobNotFoundError. Job уже не «принято» (отменён,
        взят другим воркером или завершён) — отправки нет, Job возвращается
        как есть.
        """
        job = self._repository.start_sending(job_id)
        if job is None:
            return self._repository.get(job_id)
        self._run(job)
        return job

    def cancel(self, job_id: str) -> Job:
        """Отменить Job (только из «принято»).

        Не из «принято» — InvalidTransitionError (в API это 409
        ``cannot_cancel``). Гонку с ``dispatch`` решает хранилище.
        """
        return self._repository.cancel(job_id)

    def recover_interrupted(self) -> int:
        """При старте сервиса: Job в «отправляется» -> «не доставлено», interrupted.

        Возвращает число переведённых Job (requirements.md, «Жизненный цикл»).
        """
        stuck = self._repository.find_by_state(JobState.SENDING)
        for job in stuck:
            job.interrupt()
            self._repository.save(job)
        return len(stuck)

    # ------------------------------------------------------------ внутреннее

    def _run(self, job: Job) -> None:
        """Довести Job до конечного состояния; Job не остаётся в «отправляется»."""
        try:
            self._attempts_loop(job)
        except Exception:
            # сбой в самом Dispatcher или хранилище: честный результат лучше,
            # чем Job, застрявший в «отправляется» до перезапуска
            logger.exception("сбой при отправке (notification_id=%s)", job.id)
            if job.state is JobState.SENDING:
                job.mark_undelivered("internal_error")
                self._repository.save(job)

    def _attempts_loop(self, job: Job) -> None:
        """Попытки отправки, пока Job не придёт в «доставлено» / «не доставлено»."""
        while True:
            result = self._attempt(job)

            if isinstance(result, Success):
                job.mark_delivered(result.provider_message_id)
                self._repository.save(job)
                return

            error = result.error
            give_up = (
                error.kind != "transient"  # постоянная ошибка: повтор бессмыслен
                or job.attempts >= self._policy.max_attempts  # попытки исчерпаны
                or not self._policy.accepts_pause(error.retry_after_seconds)
            )
            if give_up:
                job.mark_undelivered(error.code)
                self._repository.save(job)
                return

            self._sleep(self._policy.delay(job.attempts, error.retry_after_seconds))
            job.record_retry()  # sending -> sending, attempts + 1
            self._repository.save(job)

    def _attempt(self, job: Job) -> ToolResult:
        """Одна попытка. Никогда не бросает: любой сбой Tool превращается в Failure."""
        try:
            tool = self._registry.get(job.channel)
        except UnknownOperationError:
            logger.error(
                "канал %r не зарегистрирован (notification_id=%s)", job.channel, job.id
            )
            return Failure(
                ToolError(
                    code="internal_error",
                    kind="permanent",
                    message="канал не зарегистрирован в Registry",
                )
            )

        request = SendRequest(
            notification_id=job.id,
            attempt=job.attempts,
            params=SendParams(recipient=job.recipient, text=job.text),
        )
        try:
            result = tool.send(request)
        except Exception:
            # баг в Tool: send() не должен бросать (контракт Tool, §2)
            logger.exception(
                "Tool %r бросил исключение (notification_id=%s, attempt=%s)",
                job.channel, job.id, job.attempts,
            )
            return self._tool_bug()

        if not isinstance(result, (Success, Failure)):
            logger.error(
                "Tool %r вернул не ToolResult: %s (notification_id=%s)",
                job.channel, type(result).__name__, job.id,
            )
            return self._tool_bug()
        if isinstance(result, Failure) and not self._is_well_formed(result.error):
            logger.error(
                "Tool %r вернул Failure неверной формы (notification_id=%s)",
                job.channel, job.id,
            )
            return self._tool_bug()
        return result

    @staticmethod
    def _is_well_formed(error: object) -> bool:
        """Ответ Tool соответствует контракту (tool-contract.md, §1–2)."""
        if not isinstance(error, ToolError):
            return False
        pause = error.retry_after_seconds
        pause_ok = pause is None or (
            isinstance(pause, (int, float)) and not isinstance(pause, bool)
        )
        return (
            isinstance(error.code, str)
            and error.code in TOOL_ERROR_CODES
            and error.kind in _ERROR_KINDS
            and pause_ok
        )

    @staticmethod
    def _tool_bug() -> Failure:
        return Failure(
            ToolError(
                code="internal_error",
                kind="transient",
                message="исполнитель канала завершился с ошибкой",
            )
        )
