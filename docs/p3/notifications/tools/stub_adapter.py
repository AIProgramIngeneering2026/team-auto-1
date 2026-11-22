"""Тестовая заглушка провайдера (v1, architecture.md «Адаптер»).

Заглушка имитирует все ответы провайдера из tool-contract.md, раздел 2,
чтобы Dispatcher (следующая практика) и тесты могли прогонять весь
диапазон ошибок без сети. Неизвестный сценарий — явная ошибка
конфигурации при создании заглушки, а не молчаливый успех: молчаливое
проглатывание опечатки в тесте ("timout") делало бы тест зелёным,
проверяя совсем не то, что задумано.
"""

from __future__ import annotations

from notifications.tools.base import (
    Failure,
    ProviderAdapter,
    SendRequest,
    Success,
    ToolError,
)

#: сценарий -> (код ошибки, kind, нужна ли пауза retry_after_seconds)
_SCENARIOS: dict[str, tuple[str, str, bool]] = {
    "ok": ("", "", False),  # успех, поля ошибок не используются
    "timeout": ("provider_timeout", "transient", False),
    "unavailable": ("provider_unavailable", "transient", False),
    "rate_limited": ("rate_limited", "transient", True),
    "bad_response": ("bad_provider_response", "transient", False),
    "rejected": ("recipient_rejected", "permanent", False),
}

_MESSAGES: dict[str, str] = {
    "timeout": "канал не ответил за таймаут",
    "unavailable": "сеть недоступна или провайдер ответил 5xx",
    "rate_limited": "провайдер ответил «слишком часто» (429)",
    "bad_response": "ответ провайдера нельзя разобрать",
    "rejected": "получатель отклонён каналом (адрес/чат/бот)",
}


class StubAdapter(ProviderAdapter):
    """Заглушка канала: сценарий задаётся строкой, по умолчанию успех.

    Сценарии (имена совпадают с кодами tool-contract.md, раздел 2):

    | имя            | результат                                              |
    |----------------|--------------------------------------------------------|
    | ok             | Success с provider_message_id                          |
    | timeout        | Failure(provider_timeout, transient)                   |
    | unavailable    | Failure(provider_unavailable, transient)              |
    | rate_limited   | Failure(rate_limited, transient, retry_after_seconds)  |
    | bad_response   | Failure(bad_provider_response, transient)             |
    | rejected       | Failure(recipient_rejected, permanent)                 |

    Неизвестный сценарий — ValueError при создании (ошибка конфигурации
    теста), а не тихий успех.
    """

    __slots__ = ("scenario", "retry_after_seconds")

    def __init__(
        self, scenario: str = "ok", retry_after_seconds: int = 30
    ) -> None:
        if scenario not in _SCENARIOS:
            raise ValueError(
                f"неизвестный сценарий заглушки: {scenario!r}; "
                f"известные сценарии: {', '.join(sorted(_SCENARIOS))}"
            )
        if retry_after_seconds < 0:
            raise ValueError(
                f"retry_after_seconds не может быть отрицательным: "
                f"{retry_after_seconds}"
            )
        self.scenario = scenario
        self.retry_after_seconds = retry_after_seconds

    def deliver(self, channel: str, request: SendRequest):
        if self.scenario == "ok":
            return Success(
                provider_message_id=f"stub-{channel}-{request.notification_id}"
            )

        code, kind, with_pause = _SCENARIOS[self.scenario]
        return Failure(
            ToolError(
                code=code,
                kind=kind,
                message=f"{_MESSAGES[self.scenario]} (канал {channel})",
                retry_after_seconds=(
                    self.retry_after_seconds if with_pause else None
                ),
            )
        )
