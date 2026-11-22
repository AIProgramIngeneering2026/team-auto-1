"""TelegramTool — исполнитель канала telegram (tool-contract.md, Tool 2)."""

from __future__ import annotations

import re

from notifications.tools.base import (
    Failure,
    FieldError,
    ProviderAdapter,
    SendParams,
    SendRequest,
    Tool,
    ToolError,
    ToolResult,
)

_CHAT_ID_RE = re.compile(r"^-?\d+$")
_MAX_TEXT = 4096  # лимит Telegram


class TelegramTool(Tool):
    channel = "telegram"

    __slots__ = ("_adapter",)

    def __init__(self, adapter: ProviderAdapter) -> None:
        self._adapter = adapter

    def validate(self, params: SendParams) -> list[FieldError]:
        errors: list[FieldError] = []

        # recipient: обязателен, числовой chat_id (возможен ведущий минус)
        if not params.recipient:
            errors.append(
                FieldError("recipient", "required", "получатель обязателен")
            )
        elif not _CHAT_ID_RE.match(params.recipient):
            errors.append(
                FieldError(
                    "recipient",
                    "invalid_format",
                    "ожидается числовой chat_id",
                )
            )

        # subject: не используется, игнорируется (в том числе невалидный)

        # text: обязателен, 1..4096 символов
        if not params.text:
            errors.append(FieldError("text", "required", "текст обязателен"))
        elif len(params.text) > _MAX_TEXT:
            errors.append(
                FieldError("text", "too_long", "текст длиннее 4096 символов")
            )

        return errors

    def send(self, request: SendRequest) -> ToolResult:
        if self.validate(request.params):
            return Failure(
                ToolError(
                    code="invalid_params",
                    kind="permanent",
                    message="параметры не прошли проверку TelegramTool",
                )
            )
        return self._adapter.deliver(self.channel, request)
