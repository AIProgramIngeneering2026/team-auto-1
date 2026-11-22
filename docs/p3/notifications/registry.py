"""Registry — по имени операции (канала) выдаёт Tool.

Контракт из tool-contract.md, раздел 1:

    interface Registry:
        register(tool: Tool)
        get(channel: string) -> Tool      # неизвестный канал -> ошибка
        channels() -> list<string>

Неизвестная операция — не молчаливый None и не KeyError из глубины, а явная
ошибка UnknownOperationError с нашим кодом ``unknown_operation``. Правило
живёт в Registry: вызывающий код (входной слой, Dispatcher) просит Tool по
имени и обязан обработать только эту одну ошибку.

Dispatcher не знает конкретных Tool: он берёт нужный через Registry и
вызывает ``send()``. Новый канал добавляется новым классом Tool и
регистрацией в Registry, без правок Dispatcher.
"""

from __future__ import annotations

from notifications.errors import UnknownOperationError
from notifications.tools.base import Tool


class Registry:
    """Реестр исполнителей каналов (email / telegram / webhook)."""

    __slots__ = ("_tools",)

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Зарегистрировать Tool под именем его канала.

        Повторная регистрация того же канала — ошибка конфигурации
        (на неё указывает и tool-contract: «новый канал добавляется новым
        классом и регистрацией»), поэтому она отклоняется явно.
        """
        existing = self._tools.get(tool.channel)
        if existing is not None:
            if existing is tool:
                return  # идемпотентность: тот же объект — не ошибка
            raise ValueError(
                f"канал {tool.channel!r} уже зарегистрирован: "
                f"{type(existing).__name__}"
            )
        self._tools[tool.channel] = tool

    def get(self, operation: str) -> Tool:
        """Выдать Tool по имени операции (канала).

        Неизвестная операция -> UnknownOperationError (код unknown_operation).
        """
        try:
            return self._tools[operation]
        except KeyError:
            raise UnknownOperationError(
                operation, known=self.channels()
            ) from None

    def channels(self) -> list[str]:
        """Имена всех зарегистрированных операций, в алфавитном порядке."""
        return sorted(self._tools)


def default_registry(extra: list[Tool] | None = None) -> Registry:
    """Registry со стандартным набором Tool v1 (email/telegram/webhook).

    ``extra`` — дополнительные Tool поверх стандартного набора (для тестов
    и будущих каналов). Стандартный набор регистрируется ВСЕГДА:
    ``default_registry([])`` — это реестр с тремя каналами, а не пустой
    реестр (пустой реестр «по умолчанию» был бы ошибкой конфигурации,
    которую никто не заметил бы до первого ``get()``).

    Адаптер — тестовая заглушка (v1 подменяет реальных провайдеров,
    см. architecture.md «Адаптер внешней зависимости»).
    """
    from notifications.tools.email import EmailTool
    from notifications.tools.stub_adapter import StubAdapter
    from notifications.tools.telegram import TelegramTool
    from notifications.tools.webhook import WebhookTool

    registry = Registry()
    for tool in (
        EmailTool(StubAdapter()),
        TelegramTool(StubAdapter()),
        WebhookTool(StubAdapter()),
    ):
        registry.register(tool)
    for tool in extra or ():
        registry.register(tool)
    return registry
