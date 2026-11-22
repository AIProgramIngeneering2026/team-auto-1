"""EmailTool — исполнитель канала email (tool-contract.md, Tool 1)."""

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

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_RECIPIENT = 254
_MAX_SUBJECT = 200
_MAX_TEXT = 10_000


class EmailTool(Tool):
    channel = "email"

    __slots__ = ("_adapter",)

    def __init__(self, adapter: ProviderAdapter) -> None:
        self._adapter = adapter

    def validate(self, params: SendParams) -> list[FieldError]:
        errors: list[FieldError] = []

        # recipient: обязателен, адрес локальная@домен.зона, до 254 символов
        if not params.recipient:
            errors.append(
                FieldError("recipient", "required", "получатель обязателен")
            )
        elif len(params.recipient) > _MAX_RECIPIENT or not _EMAIL_RE.match(
            params.recipient
        ):
            errors.append(
                FieldError(
                    "recipient",
                    "invalid_format",
                    "ожидается адрес вида локальная@домен.зона",
                )
            )

        # subject: необязателен; если передан — 1..200 символов
        if params.subject is not None:
            if len(params.subject) > _MAX_SUBJECT:
                errors.append(
                    FieldError("subject", "too_long", "тема длиннее 200 символов")
                )
            elif not params.subject:
                errors.append(
                    FieldError("subject", "required", "тема пустая")
                )

        # text: обязателен, 1..10 000 символов
        if not params.text:
            errors.append(FieldError("text", "required", "текст обязателен"))
        elif len(params.text) > _MAX_TEXT:
            errors.append(
                FieldError("text", "too_long", "текст длиннее 10 000 символов")
            )

        return errors

    def send(self, request: SendRequest) -> ToolResult:
        # защитная проверка: Tool не полагается на то, что «кто-то уже
        # проверил» (контракт, раздел 3); тема по умолчанию подставляется
        # здесь, validate() её не проверяет
        if self.validate(request.params):
            return Failure(
                ToolError(
                    code="invalid_params",
                    kind="permanent",
                    message="параметры не прошли проверку EmailTool",
                )
            )
        return self._adapter.deliver(self.channel, request)
