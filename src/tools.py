"""Инструменты агента: shell, HTTP, память.

Все инструменты настоящие: shell — с жёстким allowlist команд (без shell=True),
HTTP — реальный запрос через httpx с таймаутом и ограничением размера ответа,
память — хранилище в процессе, привязанное к task_id.
"""

import asyncio
import json
import logging
import shlex
from typing import Any

import httpx

from src.security import assert_no_secrets_in_text

logger = logging.getLogger(__name__)

# Разрешены только команды чтения. Никаких rm/curl-в-пайп/интерпретаторов.
SHELL_ALLOWLIST = frozenset(
    {
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "find",
        "pwd",
        "date",
        "uname",
        "df",
        "du",
        "stat",
        "file",
        "which",
        "echo",
    }
)

SHELL_TIMEOUT_SECONDS = 15
HTTP_TIMEOUT_SECONDS = 20
HTTP_MAX_BYTES = 512 * 1024  # не тянем мегабайты в контекст модели

# Память задач: {task_id: {key: value}}
_MEMORY: dict[str, dict[str, Any]] = {}


class Tool:
    """Базовый инструмент агента."""

    def __init__(self, name: str, fn, description: str):
        self.name = name
        self.fn = fn
        self.description = description

    async def call(
        self,
        input: str,
        task_id: str,
        action: dict[str, Any] | None = None,
    ) -> Any:
        """Единый контракт с BrowserTool: input, task_id, действие планировщика."""
        args = {"input": input, "task_id": task_id, "action": action}
        assert_no_secrets_in_text(json.dumps(args, default=str), f"tool.{self.name}")
        if asyncio.iscoroutinefunction(self.fn):
            return await self.fn(**args)
        return self.fn(**args)


async def shell_tool(input: str, task_id: str, action: dict[str, Any] | None = None) -> str:
    """Выполнить команду из allowlist. Возвращает stdout/stderr и код возврата."""
    try:
        argv = shlex.split(input)
    except ValueError as e:
        return f"Ошибка разбора команды: {e}"

    if not argv:
        return "Пустая команда"

    if argv[0] not in SHELL_ALLOWLIST:
        return f"Команда '{argv[0]}' запрещена. Разрешены: " + ", ".join(sorted(SHELL_ALLOWLIST))

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=SHELL_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Команда превысила таймаут {SHELL_TIMEOUT_SECONDS} с и была прервана"

    out = stdout.decode("utf-8", errors="replace")[:2000]
    err = stderr.decode("utf-8", errors="replace")[:1000]
    return json.dumps(
        {"command": argv[0], "exit_code": proc.returncode, "stdout": out, "stderr": err},
        ensure_ascii=False,
    )


async def http_tool(input: str, task_id: str, action: dict[str, Any] | None = None) -> str:
    """Выполнить HTTP GET по URL из input. Тело ответа обрезается до HTTP_MAX_BYTES."""
    url = input.strip()
    if not url.startswith(("http://", "https://")):
        return f"Некорректный URL: {url}. Нужен http:// или https://"

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(url)
    except httpx.HTTPError as e:
        logger.warning("http_tool failed for %s: %s", url, e)
        return f"HTTP-ошибка: {e}"

    body = response.text[:HTTP_MAX_BYTES]
    return json.dumps(
        {
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "truncated": len(response.text) > HTTP_MAX_BYTES,
            "body": body,
        },
        ensure_ascii=False,
    )


async def memory_tool(input: str, task_id: str, action: dict[str, Any] | None = None) -> str:
    """Память задачи. Формат: "ключ=значение" — записать, "ключ" — прочитать, "" — всё."""
    store = _MEMORY.setdefault(task_id, {})
    text = input.strip()

    if not text:
        return json.dumps(store, ensure_ascii=False)

    if "=" in text:
        key, value = text.split("=", 1)
        key = key.strip()
        if not key:
            return "Пустой ключ памяти"
        store[key] = value.strip()
        return f"Сохранено: {key}"

    if text in store:
        return json.dumps({text: store[text]}, ensure_ascii=False)

    return f"Ключ '{text}' не найден. Известные ключи: {', '.join(store) or 'нет'}"


def reset_memory(task_id: str | None = None) -> None:
    """Очистить память задачи (или всю) — используется в тестах и при перезапуске."""
    if task_id is None:
        _MEMORY.clear()
    else:
        _MEMORY.pop(task_id, None)


__all__ = [
    "Tool",
    "shell_tool",
    "http_tool",
    "memory_tool",
    "reset_memory",
    "SHELL_ALLOWLIST",
]
