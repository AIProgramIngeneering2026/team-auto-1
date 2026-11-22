"""Злодейства З1–З6: каждое — отдельный тест, ядро его отклоняет.

Цепочки взяты из сыгранной атаки (scripts/attack.py): до починки
Job.rehydrate пропускал воскрешение Job через поддельный снимок.
Отчёт о применимости каждого злодейства — в README, раздел
«Злодейства З1–З6».

Запуск одной командой (см. README):  python3 -m pytest tests/ -v
"""

from __future__ import annotations

import pytest

from notifications.errors import InvalidTransitionError
from notifications.job import Job, JobState
from notifications.store import InMemoryJobStore
from notifications.tools.base import SendParams, SendRequest
from notifications.tools.email import EmailTool
from notifications.tools.stub_adapter import StubAdapter


def make_job(job_id="job-1"):
    return Job(
        job_id=job_id, channel="email",
        recipient="user@example.com", text="Текст",
    )


def request_for(job, attempt=1):
    return SendRequest(
        notification_id=job.id,
        attempt=attempt,
        params=SendParams(recipient=job.recipient, text=job.text),
    )


# ------------------------------------------------------------------
# З1. Двойная работа: в одном Job одновременно работают два Tool
# ------------------------------------------------------------------


def test_evil_z1_double_work():
    """З1: Job не знает исполнителей, поэтому «увидеть» двух Tool не может;
    ядро ограничивает последствия: не больше MAX_ATTEMPTS попыток на Job,
    повтор сверх лимита отклоняется явной ошибкой.
    """
    job = make_job()
    job.start_sending()

    # два Tool одновременно вызывают send() с одним notification_id —
    # это зона Dispatcher (один работник на Job); Job накапливает попытки
    email_result = EmailTool(StubAdapter("ok")).send(request_for(job))
    telegram_result = EmailTool(StubAdapter("ok")).send(request_for(job))
    assert email_result.provider_message_id
    assert telegram_result.provider_message_id

    # обе «работы» пытаются засчитать попытки, но не больше трёх
    job.record_retry()  # attempts = 2
    job.record_retry()  # attempts = 3
    with pytest.raises(InvalidTransitionError):
        job.record_retry()  # четвёртой попытки не бывает
    assert job.state is JobState.SENDING
    assert job.attempts == 3

    # после исчерпания попыток Job обязан уйти в undelivered
    job.mark_undelivered("provider_timeout")
    assert job.is_terminal


# ------------------------------------------------------------------
# З2. Вечная работа: перезапуск, а Job остался в рабочем состоянии
# ------------------------------------------------------------------


def test_evil_z2_eternal_sending():
    """З2: восстановленный sending-й Job при старте прерывается
    (ADR-0001: sending -> undelivered, error = interrupted), а «вечная
    работа» исключена: повторов не больше MAX_ATTEMPTS.
    """
    store = InMemoryJobStore()

    # перезапуск: хранилище пустое, из БД приходит снимок sending
    job = store.restore(
        job_id="job-1", channel="email",
        recipient="user@example.com", text="Текст",
        state=JobState.SENDING, attempts=2,
    )
    # ядро даёт найти такие Job и прервать их
    pending = store.jobs_in_state(JobState.SENDING)
    assert pending == [job]

    for j in pending:
        j.interrupt()
    assert job.state is JobState.UNDELIVERED
    assert job.error == "interrupted"
    assert job.is_terminal
    assert store.jobs_in_state(JobState.SENDING) == []

    # и «вечная работа» невозможна: даже без прерывания попытки кончаются
    fresh = store.restore(
        job_id="job-2", channel="email",
        recipient="user@example.com", text="Текст",
        state=JobState.SENDING, attempts=3,
    )
    with pytest.raises(InvalidTransitionError):
        fresh.record_retry()
    fresh.interrupt()
    assert fresh.is_terminal


# ------------------------------------------------------------------
# З3. Отменённое стало готовым без новой команды клиента
# ------------------------------------------------------------------


def test_evil_z3_cancelled_becomes_delivered():
    """З3: после cancel() попытка восстановить тот же id в delivered —
    отклонена явной ошибкой; Job остаётся cancelled.
    """
    store = InMemoryJobStore()
    job = make_job()
    store.add(job)
    job.cancel()
    assert store.get("job-1").state is JobState.CANCELLED

    with pytest.raises(InvalidTransitionError) as exc_info:
        store.restore(
            job_id="job-1", channel="email",
            recipient="user@example.com", text="Текст",
            state=JobState.DELIVERED, attempts=1,
        )
    assert exc_info.value.code == "invalid_transition"
    assert exc_info.value.event == "restore"
    # состояние не изменилось
    assert store.get("job-1").state is JobState.CANCELLED
    assert store.get("job-1") is job


# ------------------------------------------------------------------
# З4. Два итога: Job был в ошибке, а потом стал готовым
# ------------------------------------------------------------------


def test_evil_z4_undelivered_becomes_delivered():
    """З4: после undelivered попытка восстановить тот же id в delivered —
    отклонена; итог не меняется.
    """
    store = InMemoryJobStore()
    job = make_job()
    store.add(job)
    job.start_sending()
    job.mark_undelivered("provider_timeout")
    assert store.get("job-1").state is JobState.UNDELIVERED

    with pytest.raises(InvalidTransitionError):
        store.restore(
            job_id="job-1", channel="email",
            recipient="user@example.com", text="Текст",
            state=JobState.DELIVERED, attempts=3,
        )
    assert store.get("job-1").state is JobState.UNDELIVERED
    assert job.error == "provider_timeout"


# ------------------------------------------------------------------
# З5. Успех без работы: Job стал delivered, не побывав в работе
# ------------------------------------------------------------------


def test_evil_z5_success_without_work():
    """З5: delivered с нулём попыток — невозможная история, снимок
    отклоняется инвариантом «успех => была хотя бы одна попытка».
    """
    store = InMemoryJobStore()
    with pytest.raises(ValueError) as exc_info:
        store.restore(
            job_id="job-1", channel="email",
            recipient="user@example.com", text="Текст",
            state=JobState.DELIVERED, attempts=0,
        )
    assert "попытки" in str(exc_info.value)  # причина названа по-человечески
    # job не создан и в хранилище не появился
    from notifications.errors import JobNotFoundError

    with pytest.raises(JobNotFoundError):
        store.get("job-1")


# ------------------------------------------------------------------
# З6. Повтор успешного: готовый Job снова пошёл в работу
# ------------------------------------------------------------------


def test_evil_z6_delivered_sent_again():
    """З6: после delivered возврат в работу невозможен ни методом, ни
    восстановлением: повторная отправка — только новый Job (К5).
    """
    store = InMemoryJobStore()
    job = make_job()
    store.add(job)
    job.start_sending()
    job.mark_delivered("prov-1")
    assert store.get("job-1").state is JobState.DELIVERED

    # путь 1: «запустить снова» методом — отклонено
    with pytest.raises(InvalidTransitionError):
        job.start_sending()

    # путь 2: восстановить снимок sending для того же id — отклонено
    with pytest.raises(InvalidTransitionError):
        store.restore(
            job_id="job-1", channel="email",
            recipient="user@example.com", text="Текст",
            state=JobState.SENDING, attempts=1,
        )
    assert store.get("job-1").state is JobState.DELIVERED
    # в sending Job не попал — второй send() не может быть попыткой этого Job


# ------------------------------------------------------------------
# З0. Сломали обычный путь (только в части 3)
# ------------------------------------------------------------------


def test_evil_z0_normal_path_still_works():
    """З0 объявлено «только в части 3» и сейчас неприменимо: обычный путь
    (создание -> sending -> delivered) проверяется отдельным тестом
    test_happy_path.py — после починки дыры он проходит.
    """
    store = InMemoryJobStore()
    job = make_job()
    store.add(job)
    job.start_sending()
    result = EmailTool(StubAdapter("ok")).send(request_for(job))
    job.mark_delivered(result.provider_message_id)
    assert store.get("job-1").state is JobState.DELIVERED
