"""Неверные параметры: «ошибка неверные параметры с указанием поля».

Правила проверки знает Tool (tool-contract.md, разделы 3 и 5), проверяет
и вызывающий (до создания Job), и сам Tool в send() (защитная проверка).
Каждый неверный параметр — FieldError с именем поля как в теле запроса API.

Неизвестная операция (второе обязательное покрытие) — в test_registry.py.

Запуск одной командой (см. README):  python3 -m pytest tests/ -v
"""

from __future__ import annotations

import pytest

from notifications.tools.base import (
    Failure,
    ProviderAdapter,
    SendParams,
    SendRequest,
    Success,
)
from notifications.tools.email import EmailTool
from notifications.tools.stub_adapter import StubAdapter
from notifications.tools.telegram import TelegramTool
from notifications.tools.webhook import WebhookTool


class CountingAdapter(ProviderAdapter):
    """Адаптер-счётчик: фиксирует, был ли вызван провайдер."""

    def __init__(self) -> None:
        self.calls = 0

    def deliver(self, channel, request):
        self.calls += 1
        return Success(provider_message_id=f"stub-{channel}")


TOOLS = [
    # (tool, валидный recipient, невалидный формат получателя, лимит текста)
    (
        lambda a: EmailTool(a),
        "user@example.com",
        "не-адрес",
        10_000,
    ),
    (
        lambda a: TelegramTool(a),
        "12345678",
        "abc-not-numeric",
        4096,
    ),
    (
        lambda a: WebhookTool(a),
        "https://example.com/hook",
        "http://example.com/hook",
        65_536,
    ),
]


@pytest.mark.parametrize("make,valid_recipient,bad_recipient,limit", TOOLS)
class TestValidateParams:
    """Параметризованный набор для каждого Tool (контракт, раздел 6)."""

    def make_tool(self, make):
        return make(StubAdapter("ok"))

    def test_valid_params_pass(self, make, valid_recipient, bad_recipient, limit):
        tool = self.make_tool(make)
        errors = tool.validate(
            SendParams(recipient=valid_recipient, text="Привет!")
        )
        assert errors == []

    def test_empty_recipient_is_required(self, make, valid_recipient, bad_recipient, limit):
        tool = self.make_tool(make)
        errors = tool.validate(SendParams(recipient="", text="Текст"))
        assert errors and errors[0].field == "recipient"
        assert errors[0].code == "required"

    def test_bad_recipient_format_points_field(
        self, make, valid_recipient, bad_recipient, limit
    ):
        """Неверный формат — ошибка с указанием именно поля recipient."""
        tool = self.make_tool(make)
        errors = tool.validate(SendParams(recipient=bad_recipient, text="Текст"))
        assert errors and errors[0].field == "recipient"
        assert errors[0].code == "invalid_format"

    def test_empty_text_is_required(self, make, valid_recipient, bad_recipient, limit):
        tool = self.make_tool(make)
        errors = tool.validate(SendParams(recipient=valid_recipient, text=""))
        assert errors and errors[0].field == "text"
        assert errors[0].code == "required"

    def test_too_long_text_points_field(
        self, make, valid_recipient, bad_recipient, limit
    ):
        """Слишком длинный текст — ошибка именно по полю text."""
        tool = self.make_tool(make)
        errors = tool.validate(
            SendParams(recipient=valid_recipient, text="x" * (limit + 1))
        )
        assert errors and errors[0].field == "text"
        assert errors[0].code == "too_long"


class TestEmailSpecific:
    """Правила, специфичные для EmailTool (tool-contract.md, Tool 1)."""

    def test_params_without_subject_pass(self):
        """Без subject валидация проходит; тема подставится в send()."""
        errors = EmailTool(StubAdapter()).validate(
            SendParams(recipient="user@example.com", text="Текст")
        )
        assert errors == []

    def test_too_long_subject_points_field(self):
        errors = EmailTool(StubAdapter()).validate(
            SendParams(
                recipient="user@example.com",
                text="Текст",
                subject="с" * 201,
            )
        )
        assert errors and errors[0].field == "subject"
        assert errors[0].code == "too_long"

    def test_subject_ignored_by_telegram_and_webhook(self):
        for make, recipient, _, _ in TOOLS[1:]:
            tool = make(StubAdapter())
            errors = tool.validate(
                SendParams(recipient=recipient, text="Текст", subject="тема")
            )
            assert errors == []


class TestSendGuard:
    """Защитная проверка внутри send() (контракт, раздел 3)."""

    def _request(self, recipient, text):
        return SendRequest(
            notification_id="job-1", attempt=1,
            params=SendParams(recipient=recipient, text=text),
        )

    def test_send_with_invalid_params_fails_without_adapter_call(self):
        adapter = CountingAdapter()
        tool = EmailTool(adapter)
        result = tool.send(self._request("не-адрес", "Текст"))
        assert isinstance(result, Failure)
        assert result.error.code == "invalid_params"
        assert result.error.kind == "permanent"
        # провайдер не вызывался: параметр отвергнут до обращения к каналу
        assert adapter.calls == 0

    def test_send_with_valid_params_calls_adapter(self):
        adapter = CountingAdapter()
        tool = EmailTool(adapter)
        result = tool.send(self._request("user@example.com", "Текст"))
        assert isinstance(result, Success)
        assert adapter.calls == 1
