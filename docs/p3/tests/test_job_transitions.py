"""Тесты машины состояний Job (практика 3, задание 1).

Проверяется каждая стрелка диаграммы состояний из практики 2 и каждый
запрещённый переход: недопустимый переход должен отклоняться ЯВНОЙ
ошибкой InvalidTransitionError, состояние при этом не меняется.
"""

from __future__ import annotations

import pytest

from notifications.errors import InvalidTransitionError
from notifications.job import Job, JobState
from notifications.tools.stub_adapter import StubAdapter


def make_job(state: JobState = JobState.ACCEPTED, attempts: int = 0) -> Job:
    """Job в заданном состоянии (для тестов «из этого состояния нельзя»)."""
    job = Job(
        job_id="job-1",
        channel="email",
        recipient="user@example.com",
        text="Привет!",
    )
    if state in (JobState.SENDING, JobState.DELIVERED, JobState.UNDELIVERED):
        job.start_sending()  # accepted -> sending, attempts = 1
        for _ in range(attempts):
            job.record_retry()
    if state == JobState.DELIVERED:
        job.mark_delivered("prov-1")
    elif state == JobState.UNDELIVERED:
        job.mark_undelivered("provider_timeout")
    elif state == JobState.CANCELLED:
        job.cancel()
    assert job.state == state
    return job


ALL_EVENTS = [
    ("start_sending", []),
    ("record_retry", []),
    ("mark_delivered", ["prov-1"]),
    ("mark_undelivered", ["provider_timeout"]),
    ("interrupt", []),
    ("cancel", []),
]


# ------------------------------------------------------------------
# Разрешённые переходы — ровно стрелки диаграммы состояний
# ------------------------------------------------------------------


def test_job_created_in_accepted():
    job = make_job()
    assert job.state is JobState.ACCEPTED
    assert job.attempts == 0
    assert job.error is None
    assert not job.is_terminal


def test_accepted_to_sending():
    job = make_job()
    job.start_sending()
    assert job.state is JobState.SENDING
    assert job.attempts == 1  # первая попытка началась


def test_sending_to_sending_retry():
    job = make_job()
    job.start_sending()
    job.record_retry()
    assert job.state is JobState.SENDING  # состояние то же
    assert job.attempts == 2  # а счётчик растёт
    job.record_retry()
    assert job.attempts == 3


def test_sending_to_delivered():
    job = make_job()
    job.start_sending()
    job.mark_delivered(provider_message_id="msg-42")
    assert job.state is JobState.DELIVERED
    assert job.provider_message_id == "msg-42"
    assert job.is_terminal


def test_sending_to_undelivered():
    job = make_job()
    job.start_sending()
    job.record_retry()
    job.record_retry()
    job.mark_undelivered("provider_timeout")
    assert job.state is JobState.UNDELIVERED
    assert job.error == "provider_timeout"
    assert job.is_terminal


def test_sending_to_undelivered_interrupted():
    job = make_job()
    job.start_sending()
    job.interrupt()
    assert job.state is JobState.UNDELIVERED
    assert job.error == "interrupted"  # перезапуск сервиса, requirements.md


def test_accepted_to_cancelled():
    job = make_job()
    job.cancel()
    assert job.state is JobState.CANCELLED
    assert job.is_terminal
    assert job.attempts == 0  # отправка не начиналась, попыток нет


# ------------------------------------------------------------------
# Запрещённые переходы — явная ошибка, не молчаливое игнорирование
# ------------------------------------------------------------------


def test_cancel_from_sending_rejected():
    job = make_job(JobState.SENDING)
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.cancel()
    err = exc_info.value
    assert err.code == "invalid_transition"
    assert err.from_state == "sending"
    assert err.to_state == "cancelled"
    assert err.event == "cancel"
    # состояние не изменилось
    assert job.state is JobState.SENDING


@pytest.mark.parametrize("state", ["delivered", "undelivered", "cancelled"])
def test_cancel_from_terminal_rejected(state: str):
    job = make_job(JobState(state))
    with pytest.raises(InvalidTransitionError):
        job.cancel()
    assert job.state is JobState(state)  # терминальные состояния финальны


def test_start_sending_twice_rejected():
    job = make_job(JobState.SENDING)
    with pytest.raises(InvalidTransitionError):
        job.start_sending()
    assert job.state is JobState.SENDING
    assert job.attempts == 1  # отклонённый переход счётчик не трогает


def test_start_sending_from_cancelled_rejected():
    """Отправить уже отменённое нельзя — нужен новый Job (POST /notifications)."""
    job = make_job(JobState.CANCELLED)
    with pytest.raises(InvalidTransitionError):
        job.start_sending()
    assert job.state is JobState.CANCELLED


@pytest.mark.parametrize("state", ["delivered", "undelivered", "cancelled"])
def test_any_event_from_terminal_rejected(state: str):
    """Любое событие из терминального состояния — ошибка, Job «не оживает»."""
    job = make_job(JobState(state))
    for method_name, args in ALL_EVENTS:
        with pytest.raises(InvalidTransitionError):
            getattr(job, method_name)(*args)
    assert job.state is JobState(state)


def test_no_way_back_to_accepted():
    """Возврата «отправляется -> принято» нет: переход не определён."""
    job = make_job(JobState.SENDING)
    # единственный способ изменить состояние Job — методы-события;
    # события «в accepted» не существует, поэтому проверяем, что прямое
    # присваивание невозможно (правило живёт в Job, а не в вызывающем коде)
    with pytest.raises(AttributeError):
        job.state = JobState.ACCEPTED


def test_error_does_not_change_state_or_attempts():
    """Отклонённый переход атомарен: ни состояние, ни счётчик, ни error."""
    job = make_job(JobState.SENDING, attempts=2)
    with pytest.raises(InvalidTransitionError):
        job.cancel()
    assert job.state is JobState.SENDING
    assert job.attempts == 3  # 1 попытка + 2 повтора — не сброшены
    assert job.error is None


def test_error_message_is_explicit():
    job = make_job(JobState.DELIVERED)
    with pytest.raises(InvalidTransitionError) as exc_info:
        job.start_sending()
    message = str(exc_info.value)
    assert "delivered" in message
    assert "sending" in message
    assert "start" in message


# ------------------------------------------------------------------
# Неизменяемость содержимого Job
# ------------------------------------------------------------------


def test_job_content_is_immutable():
    job = make_job()
    for attr in ("id", "channel", "recipient", "text"):
        with pytest.raises(AttributeError):
            setattr(job, attr, "новое значение")


def test_tool_stub_adapter_independent_of_job():
    """Заглушка адаптера работает без сети (v1) и не трогает Job."""
    from notifications.tools.email import EmailTool
    from notifications.tools.base import SendParams, SendRequest

    tool = EmailTool(StubAdapter("ok"))
    result = tool.send(
        SendRequest(notification_id="job-1", attempt=1,
                    params=SendParams(recipient="user@example.com",
                                      text="Привет!"))
    )
    assert result.provider_message_id == "stub-email-job-1"
