"""Тесты Telegram-бота: сборка обработчиков, белый список, тексты ответов.

Сеть не нужна: бот не запускается, проверяется только то, что модуль
собирается и что чужие сообщения не доходят до логики. Без aiogram тесты
пропускаются — в CI пакет установлен, поэтому там они выполняются.
"""

import pytest

pytest.importorskip("aiogram")

from src.config import settings  # noqa: E402
from src.telegram_bot import (  # noqa: E402
    _format_result,
    admin_only,
    admin_only_callback,
    confirmation_keyboard,
    dp,
    handle_voice,
    is_admin,
    task_keyboard,
)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeMessage:
    """Сообщение-двойник: запоминает ответы, ничего не отправляет."""

    def __init__(self, user_id: int, text: str = ""):
        self.from_user = FakeUser(user_id)
        self.text = text
        self.answers: list[str] = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


class FakeCallback:
    def __init__(self, user_id: int, data: str = ""):
        self.from_user = FakeUser(user_id)
        self.data = data
        self.answers: list[str] = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)


def test_handlers_are_registered():
    """Модуль должен собрать обработчики: иначе бот молча ничего не делает."""
    message_names = {handler.callback.__name__ for handler in dp.message.handlers}
    callback_names = {handler.callback.__name__ for handler in dp.callback_query.handlers}

    assert {"cmd_start", "cmd_status", "handle_message", "handle_voice", "cmd_evidence"} <= message_names
    assert {"cb_control", "cb_confirmation"} <= callback_names


def test_is_admin_allows_only_configured_id():
    assert is_admin(settings.BOT_ADMIN_ID) is True
    assert is_admin(settings.BOT_ADMIN_ID + 1) is False
    assert is_admin(0) is False


@pytest.mark.asyncio
async def test_admin_only_blocks_stranger_before_logic():
    """Чужое сообщение не должно доходить до обработчика."""
    calls = []

    @admin_only
    async def handler(message):
        calls.append(message.text)
        return "ok"

    assert await handler(FakeMessage(settings.BOT_ADMIN_ID, "задача")) == "ok"

    stranger = FakeMessage(settings.BOT_ADMIN_ID + 999, "задача")
    assert await handler(stranger) is None
    assert calls == ["задача"]  # обработчик вызван ровно один раз — администратором
    assert stranger.answers == ["Доступ запрещен."]


@pytest.mark.asyncio
async def test_admin_only_callback_blocks_stranger():
    calls = []

    @admin_only_callback
    async def handler(callback):
        calls.append(callback.data)
        return "ok"

    assert await handler(FakeCallback(settings.BOT_ADMIN_ID, "confirm:t1")) == "ok"

    stranger = FakeCallback(settings.BOT_ADMIN_ID + 999, "confirm:t1")
    assert await handler(stranger) is None
    assert calls == ["confirm:t1"]
    assert stranger.answers == ["Доступ запрещен."]


@pytest.mark.asyncio
async def test_voice_message_gets_honest_refusal():
    """Голос не распознаётся — бот говорит об этом, а не выдумывает задачу."""
    message = FakeMessage(settings.BOT_ADMIN_ID)

    await handle_voice(message)

    assert len(message.answers) == 1
    assert "не распознаются" in message.answers[0]
    assert "текстом" in message.answers[0]


def test_format_result_reads_message_from_nested_payload():
    assert _format_result("t1", None) == "Задача t1: результата нет"
    assert _format_result("t1", {"status": "done", "result": {"message": "готово"}}) == "Задача t1: готово"
    assert _format_result("t1", {"status": "done", "result": "сырой текст"}) == "Задача t1: сырой текст"


def test_task_keyboard_has_control_buttons():
    buttons = [button.callback_data for row in task_keyboard("t1").inline_keyboard for button in row]

    assert buttons == ["stop:t1", "cancel:t1", "continue:t1"]


def test_confirmation_keyboard_has_confirm_and_cancel():
    buttons = [button.callback_data for row in confirmation_keyboard("t1").inline_keyboard for button in row]

    assert "confirm:t1" in buttons
    assert "cancel_action:t1" in buttons
