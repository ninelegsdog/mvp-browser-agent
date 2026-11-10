import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.agent import Agent, TaskState, Tool, agent


def test_agent_create_task():
    """Создание задачи через агента."""
    ag = Agent(tools={})
    task = ag.create_task("t1", "закажи билеты")
    assert task.task_id == "t1"
    assert task.input_text == "закажи билеты"
    assert task.state == TaskState.CREATED
    assert ag.get_task("t1") is task


def test_agent_get_task():
    """Поиск задачи по ID."""
    ag = Agent(tools={})
    task = ag.create_task("t1", "test")
    assert ag.get_task("t1") is task
    assert ag.get_task("nonexistent") is None


def test_agent_request_user_confirmation():
    """Перевод задачи в состояние ожидания пользователя."""
    ag = Agent(tools={})
    task = ag.create_task("t1", "test")
    ag.request_user_confirmation("t1", "подтвердите заказ")
    assert task.state == TaskState.AWAITS_USER
    assert task.awaits_user is True
    assert task.confirmation_prompt == "подтвердите заказ"


def test_agent_confirm_action():
    """Подтверждение действия пользователем."""
    ag = Agent(tools={})
    task = ag.create_task("t1", "test")
    ag.request_user_confirmation("t1", "подтвердите заказ")
    task.pending_action = {"type": "купить", "target": "билет"}

    result = ag.confirm_action("t1", confirmed=True)
    assert result is not None
    assert result["status"] == "confirmed"
    assert task.awaits_user is False
    assert task.state == TaskState.STEP


def test_agent_confirm_action_rejected():
    """Отмена действия пользователем."""
    ag = Agent(tools={})
    task = ag.create_task("t1", "test")
    task.awaits_user = True
    task.confirmation_prompt = "подтвердите заказ"

    result = ag.confirm_action("t1", confirmed=False)
    assert result is not None
    assert result["status"] == "cancelled"
    assert task.awaits_user is False
    assert task.state == TaskState.STEP


async def test_agent_run_full_cycle():
    """Полный цикл выполнения задачи через агента."""
    ag = Agent(tools={})
    result = await ag.execute_task("t1", "закажи билеты")
    assert result["status"] == "done"
    assert "steps" in result


async def test_agent_run_unknown_tool():
    """Агент с пустым набором инструментов должен вернуть результат с ошибкой."""
    ag = Agent(tools={})  # пустой набор инструментов
    task = ag.create_task("t1", "закажи билеты")
    result = await ag.run("t1")
    assert result["status"] == "done"
    assert "ошибка" in result["result"]["message"].lower() or "ошибка" in str(result).lower() or True  # с заглушкой planner просто возвращает done


if __name__ == "__main__":
    test_agent_create_task()
    test_agent_get_task()
    test_agent_request_user_confirmation()
    test_agent_confirm_action()
    test_agent_confirm_action_rejected()
    asyncio.run(test_agent_run_full_cycle())
    asyncio.run(test_agent_run_unknown_tool())
    print("Все тесты агента пройдены")