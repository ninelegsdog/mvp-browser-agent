"""Тесты Telegram-бота: сборка обработчиков, белый список, тексты ответов.

Сеть не нужна: бот не запускается, проверяется только то, что модуль
собирается и что чужие сообщения не доходят до логики. Без aiogram тесты
пропускаются — в CI пакет установлен, поэтому там они выполняются.
"""

import pytest

pytest.importorskip("aiogram")

from src.agent import TaskState  # noqa: E402
from src.config import settings  # noqa: E402
from src.telegram_bot import (  # noqa: E402
    _format_result,
    admin_only,
    admin_only_callback,
    agent,
    confirmation_keyboard,
    dp,
    handle_message,
    handle_voice,
    is_admin,
    task_keyboard,
)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeMessage:
    """Сообщение-двойник: запоминает ответы, ничего не отправляет."""

    def __init__(self, user_id: int, text: str = "", message_id: int = 1):
        self.from_user = FakeUser(user_id)
        self.text = text
        self.message_id = message_id
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


class FakeState:
    """FSM-состояние-двойник: хранит данные в словаре."""

    def __init__(self, data: dict | None = None):
        self.data = dict(data or {})

    async def get_data(self) -> dict:
        return dict(self.data)

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)


@pytest.mark.asyncio
async def test_clarification_updates_existing_task_instead_of_creating_new(monkeypatch):
    """Текст в чат при незавершённой задаче — уточнение, а не вторая задача."""
    import asyncio

    scheduled = []

    async def fake_run(task_id: str) -> None:
        scheduled.append(task_id)

    monkeypatch.setattr("src.telegram_bot._run_agent_task", fake_run)

    task = agent.create_task("t-clarify", "найди товар на Ozon")
    task.state = TaskState.AWAITS_USER
    task.awaits_user = True
    task.pending_action = {"type": "click", "target": "Оформить заказ"}
    state = FakeState({"current_task_id": "t-clarify"})
    tasks_before = set(agent.tasks)

    try:
        message = FakeMessage(settings.BOT_ADMIN_ID, "нет, выбери товар дешевле")
        await handle_message(message, state)
        await asyncio.sleep(0)

        assert set(agent.tasks) == tasks_before
        assert agent.get_task("t-clarify") is task
        assert any("выбери товар дешевле" in item["content"] for item in task.conversation_history)
        assert task.pending_action is None
        assert scheduled == ["t-clarify"]
        assert "Пересматриваю план" in message.answers[0]
    finally:
        agent.tasks.pop("t-clarify", None)


@pytest.mark.asyncio
async def test_message_without_active_task_creates_task(monkeypatch):
    import asyncio

    scheduled = []

    async def fake_run(task_id: str) -> None:
        scheduled.append(task_id)

    monkeypatch.setattr("src.telegram_bot._run_agent_task", fake_run)
    state = FakeState()
    message = FakeMessage(settings.BOT_ADMIN_ID, "найди билеты в Сочи", message_id=777)

    try:
        await handle_message(message, state)
        await asyncio.sleep(0)

        # Идентификатор задачи — message_id: по нему её потом находят кнопки.
        assert agent.get_task("777") is not None
        assert state.data["current_task_id"] == "777"
        assert scheduled == ["777"]
        assert "Задача принята" in message.answers[0]
    finally:
        agent.tasks.pop("777", None)
