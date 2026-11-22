"""WebhookTool — исполнитель канала webhook (tool-contract.md, Tool 3)."""

from __future__ import annotations

from urllib.parse import urlparse

from notifications.tools.base import (
    FieldError,
    ProviderAdapter,
    SendParams,
    SendRequest,
    Tool,
    ToolResult,
    invalid_params_failure,
)

_MAX_RECIPIENT = 2048
_MAX_TEXT = 65_536


class WebhookTool(Tool):
    channel = "webhook"

    __slots__ = ("_adapter",)

    def __init__(self, adapter: ProviderAdapter) -> None:
        self._adapter = adapter

    def validate(self, params: SendParams) -> list[FieldError]:
        errors: list[FieldError] = []

        # recipient: обязателен, URL со схемой https, до 2048 символов
        if not params.recipient:
            errors.append(
                FieldError("recipient", "required", "получатель обязателен")
            )
        else:
            parsed = urlparse(params.recipient)
            if (
                parsed.scheme != "https"
                or not parsed.netloc
                or len(params.recipient) > _MAX_RECIPIENT
            ):
                errors.append(
                    FieldError(
                        "recipient",
                        "invalid_format",
                        "ожидается URL со схемой https",
                    )
                )

        # subject: не используется

        # text: обязателен, 1..65 536 символов
        if not params.text.strip():
            errors.append(FieldError("text", "required", "текст обязателен"))
        elif len(params.text) > _MAX_TEXT:
            errors.append(
                FieldError("text", "too_long", "текст длиннее 65 536 символов")
            )

        return errors

    def send(self, request: SendRequest) -> ToolResult:
        errors = self.validate(request.params)
        if errors:
            return invalid_params_failure(errors)
        return self._adapter.deliver(self.channel, request)
