"""Telegram-бот: FSM-состояния задачи, кнопки стоп/отменить/продолжить, голос → текст.

Паттерн aiogram 3:
- Задача как состояние: создано → план → шаг → ждёт вас → готово.
- Кнопки управления выполнением.
- Голосовое сообщение: распознавание не подключено, бот честно просит текст.
- Необратимые действия только с подтверждением пользователя.
- Белый список BOT_ADMIN_ID: только владелец может управлять задачами.
"""

import asyncio
import functools
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.agent import TaskState, agent
from src.config import settings
from src.proof import read_only_evidence
from src.queue import TaskQueue
from src.security import mask_secret

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


def _callback_message(callback: CallbackQuery) -> Message | None:
    """Сообщение с кнопкой. Может быть недоступно — тогда молча пропускаем."""
    message = callback.message
    return message if isinstance(message, Message) else None


def is_admin(user_id: int) -> bool:
    """Проверка белого списка администраторов."""
    return bool(settings.BOT_ADMIN_ID) and user_id == settings.BOT_ADMIN_ID


def admin_only(func):
    """Декоратор: отклоняет сообщения от неадминистраторов.

    functools.wraps обязателен: aiogram 3 строит внедрение аргументов
    (message, state) по сигнатуре обработчика, а без него видит только
    *args/**kwargs.
    """

    @functools.wraps(func)
    async def wrapper(message: Message, *args, **kwargs):
        user = message.from_user
        if user is None or not is_admin(user.id):
            await message.answer("Доступ запрещен.")
            return None
        return await func(message, *args, **kwargs)

    return wrapper


def admin_only_callback(func):
    """Декоратор для callback-запросов: отклоняет неадминистраторов."""

    @functools.wraps(func)
    async def wrapper(callback: CallbackQuery, *args, **kwargs):
        user = callback.from_user
        if user is None or not is_admin(user.id):
            await callback.answer("Доступ запрещен.")
            return None
        return await func(callback, *args, **kwargs)

    return wrapper


@dp.message(CommandStart())
@admin_only
async def cmd_start(message: Message):
    await message.answer(
        "Персональный помощник-покупатель.\n"
        "Отправьте задачу текстом. "
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


@dp.message(F.voice)
@admin_only
async def handle_voice(message: Message):
    """Голос пока не распознаётся — честный отказ вместо выдуманного текста."""
    await message.answer(
        "Голосовые сообщения пока не распознаются. Отправьте задачу текстом — "
        "распознавание речи не подключено, и подставлять вместо него заглушку я не буду."
    )


@dp.message(F.text)
@admin_only
async def handle_message(message: Message, state: FSMContext):
    text = message.text or ""
    if not text.strip():
        await message.answer("Пустая задача: опишите, что нужно сделать.")
        return

    # Незавершённая задача + обычное сообщение = уточнение, а не новая задача.
    # Иначе «измени план» создаёт вторую задачу, а первая продолжает работать
    # по старому плану.
    data = await state.get_data()
    current_task_id = data.get("current_task_id")
    if current_task_id and agent.add_clarification(str(current_task_id), text):
        asyncio.create_task(_run_agent_task(str(current_task_id)))
        await message.answer(
            f"Уточнение принято для задачи {current_task_id}. Пересматриваю план.",
            reply_markup=task_keyboard(str(current_task_id)),
        )
        return

    task = agent.create_task(str(message.message_id), text)
    await state.update_data(current_task_id=task.task_id)

    # Запускаем агента асинхронно
    asyncio.create_task(_run_agent_task(task.task_id))

    await message.answer(
        f"Задача принята: {task.task_id}\nСтатус: выполняется...",
        reply_markup=task_keyboard(task.task_id),
    )


@dp.callback_query(F.data.startswith(("stop:", "cancel:", "continue:")))
@admin_only_callback
async def cb_control(callback: CallbackQuery, state: FSMContext):
    data = callback.data or ""
    if ":" not in data:
        await callback.answer("Некорректная команда")
        return
    action, task_id = data.split(":", 1)
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
        if task.state != TaskState.AWAITS_USER:
            await callback.answer("Задача уже выполняется.")
        elif task.pending_action is not None:
            # «Продолжить» — не подтверждение: отложенное действие не выполняем,
            # а просим модель продолжить без него.
            task.pending_action = None
            task.awaits_user = False
            task.state = TaskState.STEP
            task.conversation_history.append(
                {
                    "role": "user",
                    "content": "Пользователь не подтвердил предыдущее действие. Продолжай без него.",
                }
            )
            asyncio.create_task(_run_agent_task(task.task_id))
            await callback.answer("Продолжаю без неподтверждённого действия.")
        else:
            asyncio.create_task(_run_agent_task(task.task_id))
            await callback.answer("Продолжаю выполнение...")

    message = _callback_message(callback)
    if message is not None:
        await message.edit_reply_markup(reply_markup=task_keyboard(task_id))


@dp.callback_query(F.data.startswith(("confirm:", "cancel_action:", "change_plan:")))
@admin_only_callback
async def cb_confirmation(callback: CallbackQuery, state: FSMContext):
    """Обработка подтверждения/отмены действия пользователем."""
    data = callback.data or ""
    if ":" not in data:
        await callback.answer("Некорректная команда")
        return
    action_type, task_id = data.split(":", 1)
    task = agent.get_task(task_id)
    if not task or not task.awaits_user:
        await callback.answer("Нет ожидающего действия для подтверждения")
        return

    message = _callback_message(callback)

    if action_type == "confirm":
        agent.confirm_action(task_id, confirmed=True)
        await callback.answer("Подтверждено. Выполняю действие...")
        # Выполняем именно подтверждённое действие, затем продолжаем цикл
        asyncio.create_task(_resume_agent_task(task_id))
        if message is not None:
            await message.edit_text(
                f"Задача {task.task_id}\nСостояние: {task.state.value}\nСтатус: подтверждено",
                reply_markup=task_keyboard(task_id),
            )
        return

    if action_type == "cancel_action":
        agent.confirm_action(task_id, confirmed=False)
        await callback.answer("Отменено пользователем")
        # Агент пересматривает план без отменённого действия
        asyncio.create_task(_run_agent_task(task_id))
        if message is not None:
            await message.edit_text(
                f"Задача {task.task_id}\nСостояние: {task.state.value}\nСтатус: отменено",
                reply_markup=task_keyboard(task_id),
            )
        return

    # change_plan: пользователь хочет изменить формулировку — просим новый текст
    await callback.answer()
    prompt = "Опишите, что изменить в плане. Следующее сообщение заменит текущую задачу."
    if message is not None:
        await message.answer(prompt)
    else:
        await callback.answer(prompt, show_alert=True)


@dp.message(Command("evidence"))
@admin_only
async def cmd_evidence(message: Message):
    """Чтение доказательств (read-only для агента)."""
    args = (message.text or "").split()
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
        f"Доказательства для {task_id} (только чтение):\n{files}\nАгент не может модифицировать артефакты."
    )


async def _notify_admin(text: str) -> None:
    """Отправить уведомление владельцу, не роняя обработку задачи."""
    bot_instance = Bot(token=settings.BOT_TOKEN)
    try:
        await bot_instance.send_message(settings.BOT_ADMIN_ID, text)
    except Exception:
        logger.exception("Не удалось отправить уведомление администратору")
    finally:
        await bot_instance.session.close()


def _format_result(task_id: str, result: object) -> str:
    """Короткий текст результата задачи для Telegram."""
    if result is None:
        return f"Задача {task_id}: результата нет"
    if isinstance(result, dict):
        payload = result.get("result", result)
        if isinstance(payload, dict):
            payload = payload.get("message", payload)
        return f"Задача {task_id}: {str(payload)[:500]}"
    return f"Задача {task_id}: {str(result)[:500]}"


async def _run_agent_task(task_id: str) -> None:
    """Запуск агента с обработкой ошибок — сбой одной задачи не роняет бота."""
    task = agent.get_task(task_id)
    if not task:
        return

    try:
        result = await agent.run(task_id)
        logger.info("Задача %s завершена: %s", task_id, result)
        if isinstance(result, dict) and result.get("status") == "awaits_user":
            await _notify_admin(f"⏸ Задача {task_id} ждёт подтверждения:\n{result.get('prompt', '')}")
            return
        await _notify_admin("✅ " + _format_result(task_id, result))
    except Exception as e:
        logger.exception("Задача %s завершилась с ошибкой", task_id)
        await _notify_admin(f"❌ Задача {task_id} завершилась с ошибкой:\n{e}")


async def _resume_agent_task(task_id: str) -> None:
    """Продолжить задачу после подтверждения действия пользователем."""
    try:
        result = await agent.resume(task_id)
        logger.info("Задача %s продолжена: %s", task_id, result)
        if isinstance(result, dict) and result.get("status") == "awaits_user":
            await _notify_admin(f"⏸ Задача {task_id} снова ждёт подтверждения:\n{result.get('prompt', '')}")
            return
        await _notify_admin("✅ " + _format_result(task_id, result))
    except Exception as e:
        logger.exception("Не удалось продолжить задачу %s", task_id)
        await _notify_admin(f"❌ Задача {task_id} не продолжена:\n{e}")


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
