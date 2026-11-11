"""Тесты настоящих инструментов: shell (allowlist), HTTP, память."""

import json

import httpx
import pytest

from src.tools import SHELL_ALLOWLIST, http_tool, memory_tool, reset_memory, shell_tool


@pytest.mark.asyncio
async def test_shell_allowed_command_runs():
    out = json.loads(await shell_tool("pwd", "t1"))
    assert out["exit_code"] == 0
    assert out["stdout"].strip() != ""


@pytest.mark.asyncio
async def test_shell_forbidden_command_rejected():
    result = await shell_tool("rm -rf /", "t1")
    assert "запрещена" in result
    assert "rm" not in SHELL_ALLOWLIST


@pytest.mark.asyncio
async def test_shell_empty_command():
    assert await shell_tool("   ", "t1") == "Пустая команда"


@pytest.mark.asyncio
async def test_http_rejects_non_url():
    result = await http_tool("example.com", "t1")
    assert "Некорректный URL" in result


@pytest.mark.asyncio
async def test_http_real_path_with_mock_transport(monkeypatch):
    """Проверяем реальный код http_tool, подменяя только транспорт httpx."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/api"
        return httpx.Response(200, text='{"ok": true}', headers={"content-type": "application/json"})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    out = json.loads(await http_tool("https://example.com/api", "t1"))
    assert out["status_code"] == 200
    assert out["body"] == '{"ok": true}'
    assert out["truncated"] is False


@pytest.mark.asyncio
async def test_memory_write_read_roundtrip():
    reset_memory("t-mem")
    assert await memory_tool("city=Сочи", "t-mem") == "Сохранено: city"
    assert json.loads(await memory_tool("city", "t-mem")) == {"city": "Сочи"}
    assert json.loads(await memory_tool("", "t-mem")) == {"city": "Сочи"}
    assert "не найден" in await memory_tool("missing", "t-mem")


@pytest.mark.asyncio
async def test_memory_isolated_per_task():
    reset_memory()
    await memory_tool("k=v1", "task-a")
    await memory_tool("k=v2", "task-b")
    assert json.loads(await memory_tool("k", "task-a")) == {"k": "v1"}
    assert json.loads(await memory_tool("k", "task-b")) == {"k": "v2"}
