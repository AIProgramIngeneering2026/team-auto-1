"""Тесты Registry (практика 3, задание 2).

Registry выдаёт Tool по имени операции. Неизвестная операция — явная
ошибка UnknownOperationError с нашим кодом ``unknown_operation``.
"""

from __future__ import annotations

import pytest

from notifications.errors import NotificationError, UnknownOperationError
from notifications.registry import Registry, default_registry
from notifications.tools.email import EmailTool
from notifications.tools.stub_adapter import StubAdapter
from notifications.tools.telegram import TelegramTool
from notifications.tools.webhook import WebhookTool


@pytest.fixture()
def registry() -> Registry:
    return default_registry()


# ------------------------------------------------------------------
# Известные операции: по имени выдаётся нужный Tool
# ------------------------------------------------------------------


def test_get_returns_tool_for_each_channel(registry: Registry):
    email_tool = registry.get("email")
    telegram_tool = registry.get("telegram")
    webhook_tool = registry.get("webhook")

    assert isinstance(email_tool, EmailTool)
    assert isinstance(telegram_tool, TelegramTool)
    assert isinstance(webhook_tool, WebhookTool)

    assert email_tool.channel == "email"
    assert telegram_tool.channel == "telegram"
    assert webhook_tool.channel == "webhook"


def test_get_returns_same_instance(registry: Registry):
    assert registry.get("email") is registry.get("email")


def test_channels_lists_all_operations_sorted(registry: Registry):
    assert registry.channels() == ["email", "telegram", "webhook"]


# ------------------------------------------------------------------
# Неизвестная операция — явная ошибка, не None и не молчание
# ------------------------------------------------------------------


@pytest.mark.parametrize("unknown", ["sms", "sms2", "Email", "", "email "])
def test_unknown_operation_is_explicit_error(registry: Registry, unknown: str):
    with pytest.raises(UnknownOperationError) as exc_info:
        registry.get(unknown)
    err = exc_info.value
    # наш собственный код ошибки
    assert err.code == "unknown_operation"
    # сообщение называет операцию и подсказывает известные
    assert unknown in err.message
    assert "email" in err.message  # список известных операций в подсказке
    assert err.operation == unknown


def test_unknown_operation_error_is_domain_error():
    """UnknownOperationError входит в иерархию доменных ошибок."""
    assert issubclass(UnknownOperationError, NotificationError)


def test_get_on_empty_registry():
    empty = Registry()
    with pytest.raises(UnknownOperationError) as exc_info:
        empty.get("email")
    assert exc_info.value.code == "unknown_operation"
    assert exc_info.value.known == []  # подсказывать нечего


# ------------------------------------------------------------------
# Регистрация
# ------------------------------------------------------------------


def test_register_new_tool_extends_registry():
    registry = Registry()
    assert registry.channels() == []
    registry.register(EmailTool(StubAdapter()))
    assert registry.channels() == ["email"]
    assert isinstance(registry.get("email"), EmailTool)


def test_register_same_tool_twice_is_idempotent():
    registry = Registry()
    tool = TelegramTool(StubAdapter())
    registry.register(tool)
    registry.register(tool)  # тот же объект — не ошибка
    assert registry.channels() == ["telegram"]


def test_register_duplicate_channel_rejected():
    registry = Registry()
    registry.register(EmailTool(StubAdapter()))
    with pytest.raises(ValueError):  # ошибка конфигурации, канал занят
        registry.register(EmailTool(StubAdapter()))
    assert registry.channels() == ["email"]


def test_new_channel_needs_no_dispatcher_changes(registry: Registry):
    """Контракт: новый канал — новый класс Tool + регистрация, и только."""

    class SmsTool:
        # минимальный Tool-подобный исполнитель для проверки расширения
        channel = "sms"

        def validate(self, params):
            return []

        def send(self, request):
            from notifications.tools.base import Success

            return Success(provider_message_id=None)

    registry.register(SmsTool())
    assert registry.channels() == ["email", "sms", "telegram", "webhook"]
    assert isinstance(registry.get("sms"), SmsTool)
