"""Демо практики 3: Job с проверкой переходов + Registry.

Запуск из корня проекта:  python3 examples/demo.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# запуск из корня проекта без установки пакета
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from notifications.errors import InvalidTransitionError, UnknownOperationError
from notifications.job import Job
from notifications.registry import default_registry
from notifications.tools.base import Failure, SendParams, SendRequest
from notifications.tools.email import EmailTool
from notifications.tools.stub_adapter import StubAdapter


def main() -> None:
    registry = default_registry()

    # --- задание 2: Registry выдаёт Tool по имени операции --------------
    for channel in registry.channels():
        print(f"registry.get({channel!r}) -> {type(registry.get(channel)).__name__}")

    try:
        registry.get("sms2")
    except UnknownOperationError as err:
        print("\nregistry.get('sms2') -> UnknownOperationError:")
        print(f"  code    = {err.code}")
        print(f"  message = {err.message}")

    # --- задание 1: Job с проверкой переходов ----------------------------
    # Порядок как в реальном потоке (architecture.md, «Поток запроса»):
    # состояние -> отправка -> результат -> переход в терминальное состояние
    job = Job(
        job_id=str(uuid.uuid4()),
        channel="email",
        recipient="user@example.com",
        text="Привет!",
    )
    print(f"\nсоздан Job: state={job.state.value}, attempts={job.attempts}")

    # 1) Dispatcher взял Job в работу: accepted -> sending, попытка №1
    job.start_sending()
    print(f"start_sending: state={job.state.value}, attempts={job.attempts}")

    # 2) через Registry взят исполнитель канала и выполнена попытка send()
    tool = registry.get(job.channel)
    result = tool.send(
        SendRequest(
            notification_id=job.id,
            attempt=job.attempts,
            params=SendParams(recipient=job.recipient, text=job.text),
        )
    )
    print(f"tool.send(...) -> {result}")

    # 3) по результату попытки — терминальный переход
    job.mark_delivered(result.provider_message_id)
    print(f"mark_delivered: state={job.state.value}")

    # недопустимый переход после доставки — явная ошибка
    try:
        job.cancel()
    except InvalidTransitionError as err:
        print("\ncancel() после доставки -> InvalidTransitionError:")
        print(f"  code    = {err.code}")
        print(f"  message = {err.message}")
        print(f"  состояние Job не изменилось: {job.state.value}")

    # --- заглушка провайдера: все сценарии ошибок канала -----------------
    # полный набор сценариев tool-contract.md §2; неизвестный сценарий —
    # ошибка конфигурации, а не тихий Success
    try:
        StubAdapter("timout")
    except ValueError as err:
        print(f"\nStubAdapter('timout') -> ValueError: {err}")

    # временная ошибка: Job остаётся в sending, попытка засчитана,
    # Dispatcher повторит не раньше retry_after_seconds
    flaky = Job(
        job_id=str(uuid.uuid4()),
        channel="email",
        recipient="user@example.com",
        text="Ещё раз привет",
    )
    flaky.start_sending()
    rate_limited = EmailTool(StubAdapter("rate_limited", retry_after_seconds=7))
    failure = rate_limited.send(
        SendRequest(
            notification_id=flaky.id,
            attempt=flaky.attempts,
            params=SendParams(recipient=flaky.recipient, text=flaky.text),
        )
    )
    assert isinstance(failure, Failure)
    print(f"\nсценарий rate_limited -> {failure}")
    print(f"  state={flaky.state.value}: повтор не меняет состояние,")
    flaky.record_retry()
    print(f"  record_retry: state={flaky.state.value}, attempts={flaky.attempts}")
    print(
        f"  Dispatcher повторит попытку не раньше чем через "
        f"{failure.error.retry_after_seconds} с"
    )


if __name__ == "__main__":
    main()
