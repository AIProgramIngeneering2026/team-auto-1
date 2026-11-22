"""Тесты заглушки провайдера и исправлений практики 3 (ревью).

Проверяется:
- все сценарии tool-contract.md §2 существуют и дают верный код/kind;
- rate_limited несёт retry_after_seconds;
- неизвестный сценарий — явная ошибка конфигурации, не молчаливый успех;
- default_registry([]) возвращает стандартный набор, а не пустой реестр;
- mark_undelivered отклоняет пустую причину;
- Job.rehydrate — единственная точка восстановления, с защитой;
- конструктор Job не позволяет задать состояние напрямую.
"""

from __future__ import annotations

import pytest

from notifications.job import Job, JobState
from notifications.registry import Registry, default_registry
from notifications.tools.base import SendParams, SendRequest
from notifications.tools.email import EmailTool
from notifications.tools.stub_adapter import StubAdapter


def send_with(adapter: StubAdapter, channel: str = "email"):
    tool = EmailTool(adapter)
    return tool.send(
        SendRequest(
            notification_id="job-x",
            attempt=1,
            params=SendParams(recipient="user@example.com", text="Текст"),
        )
    )


# ------------------------------------------------------------------
# Сценарии заглушки: полный набор ошибок tool-contract.md, раздел 2
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario,code,kind",
    [
        ("timeout", "provider_timeout", "transient"),
        ("unavailable", "provider_unavailable", "transient"),
        ("rate_limited", "rate_limited", "transient"),
        ("bad_response", "bad_provider_response", "transient"),
        ("rejected", "recipient_rejected", "permanent"),
    ],
)
def test_scenario_maps_to_contract_code(scenario, code, kind):
    result = send_with(StubAdapter(scenario))
    assert result.error.code == code
    assert result.error.kind == kind


def test_ok_scenario_returns_success():
    result = send_with(StubAdapter("ok"))
    assert result.provider_message_id == "stub-email-job-x"


def test_rate_limited_carries_retry_after():
    result = send_with(StubAdapter("rate_limited", retry_after_seconds=7))
    assert result.error.retry_after_seconds == 7


def test_only_rate_limited_has_retry_after():
    for scenario in ("timeout", "unavailable", "bad_response", "rejected"):
        result = send_with(StubAdapter(scenario))
        assert result.error.retry_after_seconds is None


# ------------------------------------------------------------------
# Неизвестный сценарий — явная ошибка, не молчаливый успех
# ------------------------------------------------------------------


@pytest.mark.parametrize("typo", ["timout", "unavilable", "OK", ""])
def test_unknown_scenario_is_config_error(typo):
    with pytest.raises(ValueError) as exc_info:
        StubAdapter(typo)
    # сообщение называет опечатку и подсказывает известные сценарии
    assert typo in str(exc_info.value)
    assert "rate_limited" in str(exc_info.value)


def test_negative_retry_after_rejected():
    with pytest.raises(ValueError):
        StubAdapter("rate_limited", retry_after_seconds=-1)


# ------------------------------------------------------------------
# default_registry([]) — стандартный набор, а не пустой реестр
# ------------------------------------------------------------------


def test_default_registry_with_empty_list_is_not_empty():
    registry = default_registry([])
    assert registry.channels() == ["email", "telegram", "webhook"]


def test_default_registry_with_none_is_default():
    assert default_registry().channels() == ["email", "telegram", "webhook"]


def test_default_registry_extra_adds_channel():
    class PushTool:
        channel = "push"

        def validate(self, params):
            return []

        def send(self, request):
            from notifications.tools.base import Success

            return Success()

    registry = default_registry(extra=[PushTool()])
    assert registry.channels() == ["email", "push", "telegram", "webhook"]


def test_truly_empty_registry_is_explicit():
    """Пустой реестр можно собрать только явно через Registry()."""
    assert Registry().channels() == []


# ------------------------------------------------------------------
# mark_undelivered: пустая причина — ошибка
# ------------------------------------------------------------------


def make_sending_job() -> Job:
    job = Job(job_id="job-1", channel="email",
              recipient="user@example.com", text="Текст")
    job.start_sending()
    return job


def test_mark_undelivered_empty_error_rejected():
    job = make_sending_job()
    with pytest.raises(ValueError):
        job.mark_undelivered("")
    with pytest.raises(ValueError):
        job.mark_undelivered(None)  # type: ignore[arg-type]
    # отклонённый вызов состояние не меняет
    assert job.state is JobState.SENDING
    assert job.error is None


# ------------------------------------------------------------------
# rehydrate: восстановление из хранилища с защитой
# ------------------------------------------------------------------


def test_constructor_creates_only_accepted():
    job = Job(job_id="job-1", channel="email",
              recipient="user@example.com", text="Текст")
    assert job.state is JobState.ACCEPTED
    assert job.attempts == 0
    assert job.error is None


def test_constructor_rejects_empty_content():
    with pytest.raises(ValueError):
        Job(job_id="", channel="email",
            recipient="user@example.com", text="Текст")


def test_constructor_has_no_state_parameter():
    """Подсунуть состояние через конструктор больше нельзя."""
    with pytest.raises(TypeError):
        Job(job_id="job-1", channel="email",
            recipient="user@example.com", text="Текст",
            state=JobState.DELIVERED)  # type: ignore[call-arg]


def test_rehydrate_restores_snapshot():
    job = Job.rehydrate(
        job_id="job-1", channel="email",
        recipient="user@example.com", text="Текст",
        state=JobState.UNDELIVERED, attempts=3, error="provider_timeout",
    )
    assert job.state is JobState.UNDELIVERED
    assert job.attempts == 3
    assert job.error == "provider_timeout"
    assert job.is_terminal


def test_rehydrate_undelivered_without_error_rejected():
    with pytest.raises(ValueError):
        Job.rehydrate(
            job_id="job-1", channel="email",
            recipient="user@example.com", text="Текст",
            state=JobState.UNDELIVERED, attempts=3, error=None,
        )


def test_rehydrate_rejects_raw_string_state():
    with pytest.raises(ValueError):
        Job.rehydrate(
            job_id="job-1", channel="email",
            recipient="user@example.com", text="Текст",
            state="delivered",  # type: ignore[arg-type]
        )


def test_rehydrated_job_still_follows_state_machine():
    """Восстановленный Job живёт по тем же правилам переходов."""
    job = Job.rehydrate(
        job_id="job-1", channel="email",
        recipient="user@example.com", text="Текст",
        state=JobState.DELIVERED, attempts=1,
        provider_message_id="prov-1",
    )
    from notifications.errors import InvalidTransitionError

    with pytest.raises(InvalidTransitionError):
        job.cancel()
    assert job.state is JobState.DELIVERED
