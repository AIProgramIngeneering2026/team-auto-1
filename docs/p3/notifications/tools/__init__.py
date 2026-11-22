"""Исполнители каналов: Tool, модели и заглушка адаптера."""

from notifications.tools.base import (
    Failure,
    FieldError,
    ProviderAdapter,
    SendParams,
    SendRequest,
    Success,
    Tool,
    ToolError,
    ToolResult,
)
from notifications.tools.email import EmailTool
from notifications.tools.stub_adapter import StubAdapter
from notifications.tools.telegram import TelegramTool
from notifications.tools.webhook import WebhookTool

__all__ = [
    "Tool",
    "ProviderAdapter",
    "StubAdapter",
    "EmailTool",
    "TelegramTool",
    "WebhookTool",
    "SendParams",
    "SendRequest",
    "ToolResult",
    "Success",
    "Failure",
    "ToolError",
    "FieldError",
]
