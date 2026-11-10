"""Telegram-бот: FSM-состояния задачи, кнопки стоп/отменить/продолжить, голос → текст.

Паттерн aiogram 3:
- Задача как состояние: создано → план → шаг → ждёт вас → готово.
- Кнопки управления выполнением.
- Голосовое сообщение → текст (заглушка, в проде — SpeechKit/Whisper).
- Необратимые действия только с подтверждением пользователя.
- Белый список BOT_ADMIN_ID: только владелец может управлять задачами.
"""

import asyncio
import logging
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

from src.config import settings
from src.agent import agent, AgentTask, TaskState
from src.queue import TaskQueue
from src.proof import read_only_evidence
from src.security import mask_secret, assert_no_secrets_in_text

logger = logging.getLogger(__name__)

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
queue = TaskQueue()


class TaskStates(StatesGroup):
    awaiting_input = State()


def task_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Клавиатура управления задачей."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏹ Стоп", callback_data=f"stop:{task_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel:{task_id}"),
                InlineKeyboardButton(text="▶ Продолжить", callback_data=f"continue:{task_id}"),
            ]
        ]
    )


def confirmation_keyboard(task_id: str) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения действия."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{task_id}"),
                InlineKeyboardButton(text="❌ Отменить", callback_data=f"cancel_action:{task_id}"),
                InlineKeyboardButton(text="✏️ Изменить план", callback_data=f"change_plan:{task_id}"),
            ]
        ]
    )


def is_admin(user_id: int) -> bool:
    """Проверка белого списка администраторов."""
    return settings.BOT_ADMIN_ID and user_id == settings.BOT_ADMIN_ID


def admin_only(func):
    """Декоратор: отклоняет сообщения от неадминистраторов."""

    async def wrapper(message: Message, *args, **kwargs):
        if not is_admin(message.from_user.id):
            await message.answer("Доступ запрещен.")
            return
        return await func(message, *args, **kwargs)

    return wrapper


def admin_only_callback(func):
    """Декоратор для callback-запросов: отклоняет неадминистраторов."""

    async def wrapper(callback: CallbackQuery, *args, **kwargs):
        if not is_admin(callback.from_user.id):
            await callback.answer("Доступ запрещен.")
            return
        return await func(callback, *args, **kwargs)

    return wrapper


@dp.message(CommandStart())
@admin_only
async def cmd_start(message: Message):
    await message.answer(
        "Персональный помощник-покупатель.\n"
        "Отправьте задачу текстом или голосом. "
        "Примеры: «закажи билеты в Сочи», «найди товар на Ozon», «прочти письма».\n\n"
        "Я буду выполнять действия в браузере и запрашивать ваше подтверждение "
        "перед необратимыми действиями (оплата, оформление заказа)."
    )


@dp.message(Command("status"))
@admin_only
async def cmd_status(message: Message, state: FSMContext):
    data = await state.get_data()
    task_id = data.get("current_task_id")
    if not task_id:
        await message.answer("Нет активной задачи.")
        return
    task = agent.get_task(task_id)
    if not task:
        await message.answer("Задача не найдена.")
        return
    status_text = (
        f"Задача {task.task_id}\n"
        f"Состояние: {task.state.value}\n"
        f"Шаг: {task.current_step}\n"
        f"Задача: {task.input_text[:80]}"
    )
    if task.awaits_user:
        status_text += f"\n\nОжидает вашего подтверждения:\n{task.confirmation_prompt}"
    await message.answer(
        status_text,
        reply_markup=task_keyboard(task_id),
    )


@dp.message(F.text | F.voice)
@admin_only
async def handle_message(message: Message, state: FSMContext):
    text = message.text or "[голосовое сообщение]"
    task = agent.create_task(str(message.message_id), text)
    await state.update_data(current_task_id=task.task_id)

    # Запускаем агента асинхронно
    asyncio.create_task(_run_agent_task(task.task_id))

    await message.answer(
        f"Задача принята: {task.task_id}\n"
        f"Статус: выполняется...",
        reply_markup=task_keyboard(task.task_id),
    )


@dp.callback_query(F.data.startswith(("stop:", "cancel:", "continue:")))
@admin_only_callback
async def cb_control(callback: CallbackQuery, state: FSMContext):
    action, task_id = callback.data.split(":", 1)
    task = agent.get_task(task_id)
    if not task:
        await callback.answer("Задача не найдена")
        return

    if action == "stop":
        task.state = TaskState.AWAITS_USER
        await callback.answer("Остановлено. Можно продолжить позже.")
    elif action == "cancel":
        task.state = TaskState.FAILED
        await callback.answer("Отменено.")
    elif action == "continue":
        if task.state == TaskState.AWAITS_USER:
            asyncio.create_task(_run_agent_task(task.task_id))
            await callback.answer("Продолжаю выполнение...")
        else:
            await callback.answer("Задача уже выполняется.")

    await callback.message.edit_reply_markup(reply_markup=task_keyboard(task_id))


@dp.callback_query(F.data.startswith(("confirm:", "cancel_action:", "change_plan:")))
@admin_only_callback
async def cb_confirmation(callback: CallbackQuery, state: FSMContext):
    """Обработка подтверждения/отмены действия пользователем."""
    action_type, task_id = callback.data.split(":", 1)
    task = agent.get_task(task_id)
    if not task or not task.awaits_user:
        await callback.answer("Нет ожидающего действия для подтверждения")
        return

    if action_type == "confirm":
        result = agent.confirm_action(task_id, confirmed=True)
        await callback.answer("Подтверждено. Продолжаю выполнение...")
        await callback.message.edit_reply_markup(reply_markup=task_keyboard(task_id))
        # Продолжаем выполнение агента
        asyncio.create_task(_run_agent_task(task_id))
    elif action_type == "cancel_action":
        result = agent.confirm_action(task_id, confirmed=False)
        await callback.answer("Отменено пользователем")
        await callback.message.edit_reply_markup(reply_markup=task_keyboard(task_id))
        # Агент должен пересмотреть план
        asyncio.create_task(_run_agent_task(task_id))
    elif action_type == "change_plan":
        await callback.answer("Изменение плана - пока не реализовано. Используйте новую задачу.")

    await callback.message.edit_text(
        f"Задача {task.task_id}\n"
        f"Состояние: {task.state.value}\n"
        f"Статус: {result.get('status', 'неизвестно') if result else 'неизвестно'}",
        reply_markup=task_keyboard(task_id),
    )


@dp.message(Command("evidence"))
@admin_only
async def cmd_evidence(message: Message):
    """Чтение доказательств (read-only для агента)."""
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Укажите task_id: /evidence <id>")
        return
    task_id = args[1]
    info = read_only_evidence(task_id)
    if not info.get("exists"):
        await message.answer(f"Нет доказательств для {task_id}")
        return
    files = "\n".join(info["files"])
    await message.answer(
        f"Доказательства для {task_id} (только чтение):\n{files}\n"
        f"Агент не может модифицировать артефакты."
    )


async def _run_agent_task(task_id: str) -> None:
    """Запуск агента с обработкой ошибок — сбой одной задачи не роняет бота."""
    task = agent.get_task(task_id)
    if not task:
        return

    try:
        result = await agent.run(task_id)
        logger.info(f"Задача {task_id} завершена: {result}")

        # Отправляем результат пользователю
        bot_instance = Bot(token=settings.BOT_TOKEN)
        try:
            await bot_instance.send_message(
                settings.BOT_ADMIN_ID,
                f"✅ Задача {task_id} выполнена\n"
                f"Результат: {result.get('result', str(result)[:500]) if isinstance(result, dict) else result}",
            )
        finally:
            await bot_instance.session.close()
    except Exception as e:
        logger.error(f"Задача {task_id} завершилась с ошибкой: {e}")
        bot_instance = Bot(token=settings.BOT_TOKEN)
        try:
            await bot_instance.send_message(
                settings.BOT_ADMIN_ID,
                f"❌ Задача {task_id} завершилась с ошибкой:\n{str(e)}",
            )
        finally:
            await bot_instance.session.close()


async def on_startup(dispatcher: Dispatcher):
    await queue.connect()
    logger.info("Бот запущен")


async def on_shutdown(dispatcher: Dispatcher):
    await queue.close()
    await bot.session.close()
    logger.info("Бот остановлен")


def main():
    logging.basicConfig(level=settings.LOG_LEVEL.upper(), format="%(levelname)s %(name)s: %(message)s")
    logger.info(f"Токен бота: {mask_secret(settings.BOT_TOKEN)}")
    logger.info(f"Администратор: {settings.BOT_ADMIN_ID}")

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    asyncio.run(dp.start_polling(bot))


if __name__ == "__main__":
    main()