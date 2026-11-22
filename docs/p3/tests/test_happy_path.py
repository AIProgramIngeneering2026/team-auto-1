"""Обычный путь Job — от создания до успешного завершения (требование практики).

Путь повторяет сценарий С1 и «Поток запроса» из architecture.md:

    создание (accepted) -> Dispatcher взял в работу (sending, attempt 1)
    -> Tool из Registry -> send() -> канал подтвердил -> delivered.

Второй тест — тот же путь с временной ошибкой канала и повторной попыткой
(сценарий С5): Job остаётся в sending, attempts растёт, вторая попытка
приносит успех.

Запуск одной командой (см. README):  python3 -m pytest tests/ -v
"""

from __future__ import annotations

from notifications.job import Job, JobState
from notifications.registry import default_registry
from notifications.store import InMemoryJobStore
from notifications.tools.base import (
    Failure,
    SendParams,
    SendRequest,
    Success,
)
from notifications.tools.stub_adapter import StubAdapter


def make_params(recipient="user@example.com", text="Привет!"):
    return SendParams(recipient=recipient, text=text)


def make_request(job):
    return SendRequest(
        notification_id=job.id,
        attempt=job.attempts,
        params=SendParams(recipient=job.recipient, text=job.text),
    )


def test_happy_path_from_creation_to_delivered():
    """Обычный путь: создание -> sending -> send() -> delivered."""
    registry = default_registry()
    store = InMemoryJobStore()

    # входной слой: валидный запрос, новый Job в accepted
    job = Job(
        job_id="job-1", channel="email",
        recipient="user@example.com", text="Привет!",
    )
    store.add(job)
    assert store.get("job-1").state is JobState.ACCEPTED
    assert store.get("job-1") is job  # один живой объект на id

    # Dispatcher взял Job в работу: первая попытка началась
    job.start_sending()
    assert store.get("job-1").state is JobState.SENDING
    assert job.attempts == 1

    # Dispatcher берёт Tool из Registry по имени канала и вызывает send()
    tool = registry.get(job.channel)
    result = tool.send(make_request(job))
    assert isinstance(result, Success)
    assert result.provider_message_id == "stub-email-job-1"

    # канал подтвердил приём — терминальное состояние
    job.mark_delivered(result.provider_message_id)
    assert store.get("job-1").state is JobState.DELIVERED
    assert job.attempts == 1
    assert job.is_terminal
    assert job.provider_message_id == "stub-email-job-1"


def test_happy_path_with_transient_error_and_retry():
    """Обычный путь с временной ошибкой: попытка, повтор, успех (С5)."""
    registry = default_registry()
    store = InMemoryJobStore()

    job = Job(
        job_id="job-2", channel="email",
        recipient="user@example.com", text="Ещё раз привет",
    )
    store.add(job)
    job.start_sending()

    # первая попытка — таймаут (временная ошибка)
    flaky_tool = registry.get(job.channel)
    flaky_tool._adapter = StubAdapter("timeout")  # подмена адаптера (v1)
    first = flaky_tool.send(make_request(job))
    assert isinstance(first, Failure)
    assert first.error.code == "provider_timeout"
    assert first.error.kind == "transient"

    # Dispatcher повторяет: состояние не меняется, attempts растёт
    job.record_retry()
    assert job.state is JobState.SENDING
    assert job.attempts == 2

    # вторая попытка — успех
    healthy_tool = registry.get(job.channel)
    healthy_tool._adapter = StubAdapter("ok")
    second = healthy_tool.send(make_request(job))
    assert isinstance(second, Success)

    job.mark_delivered(second.provider_message_id)
    assert store.get("job-2").state is JobState.DELIVERED
    assert job.attempts == 2
    assert job.is_terminal
