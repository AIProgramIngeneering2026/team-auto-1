"""EmailTool — исполнитель канала email (tool-contract.md, Tool 1)."""

from __future__ import annotations

import re
from dataclasses import replace

from notifications.tools.base import (
    FieldError,
    ProviderAdapter,
    SendParams,
    SendRequest,
    Tool,
    ToolResult,
    invalid_params_failure,
)

_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_MAX_RECIPIENT = 254
_MAX_SUBJECT = 200
_MAX_TEXT = 10_000

#: Тема письма, если клиент её не передал (в v1 из HTTP API subject не приходит).
#: В продакшене значение берётся из конфигурации (tool-contract.md, Tool 1).
DEFAULT_SUBJECT = "Уведомление"


class EmailTool(Tool):
    channel = "email"

    __slots__ = ("_adapter", "_default_subject")

    def __init__(self, adapter: ProviderAdapter, default_subject: str = DEFAULT_SUBJECT) -> None:
        if not default_subject.strip():
            raise ValueError("default_subject не может быть пустым")
        self._adapter = adapter
        self._default_subject = default_subject

    def validate(self, params: SendParams) -> list[FieldError]:
        errors: list[FieldError] = []

        # recipient: обязателен, адрес локальная@домен.зона, до 254 символов
        if not params.recipient:
            errors.append(
                FieldError("recipient", "required", "получатель обязателен")
            )
        elif len(params.recipient) > _MAX_RECIPIENT or not _EMAIL_RE.fullmatch(
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
            elif not params.subject.strip():
                errors.append(
                    FieldError("subject", "required", "тема пустая")
                )

        # text: обязателен, 1..10 000 символов (из одних пробелов — пустой)
        if not params.text.strip():
            errors.append(FieldError("text", "required", "текст обязателен"))
        elif len(params.text) > _MAX_TEXT:
            errors.append(
                FieldError("text", "too_long", "текст длиннее 10 000 символов")
            )

        return errors

    def send(self, request: SendRequest) -> ToolResult:
        # защитная проверка: Tool не полагается на то, что «кто-то уже
        # проверил» (контракт, раздел 3)
        errors = self.validate(request.params)
        if errors:
            return invalid_params_failure(errors)
        # тема по умолчанию подставляется здесь, validate() её не проверяет
        if request.params.subject is None:
            request = replace(
                request, params=replace(request.params, subject=self._default_subject)
            )
        return self._adapter.deliver(self.channel, request)
