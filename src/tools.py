"""Инструменты агента: shell, HTTP, память.

Все инструменты синхронные/асинхронные функции с проверкой секретов.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

from src.security import assert_no_secrets_in_text

logger = logging.getLogger(__name__)


class Tool:
    """Базовый инструмент агента."""

    def __init__(self, name: str, fn: Callable, description: str):
        self.name = name
        self.fn = fn
        self.description = description

    async def call(self, **kwargs) -> Any:
        assert_no_secrets_in_text(json.dumps(kwargs), f"tool.{self.name}")
        if asyncio.iscoroutinefunction(self.fn):
            return await self.fn(**kwargs)
        return self.fn(**kwargs)


async def shell_tool(input: str, task_id: str, action: Optional[Dict[str, Any]] = None) -> str:
    """Выполнить безопасную команду shell (только чтение в демо)."""
    await asyncio.sleep(0.2)
    return f"shell executed: {input[:50]}"


async def http_tool(input: str, task_id: str, action: Optional[Dict[str, Any]] = None) -> str:
    """HTTP-запрос к внешнему сервису."""
    await asyncio.sleep(0.3)
    return f"http response for: {input[:50]}"


async def memory_tool(input: str, task_id: str, action: Optional[Dict[str, Any]] = None) -> str:
    """Чтение/запись в памяти задачи (состояние, предпочтения)."""
    await asyncio.sleep(0.1)
    return f"memory: {input[:50]}"


# Экспорт для удобства импорта
__all__ = ["Tool", "shell_tool", "http_tool", "memory_tool"]