import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.agent import Agent, TaskState, Tool, TOOLS


def test_agent_create_task():
    """Создание задачи через агента."""
    ag = Agent(TOOLS)
    task = ag.create_task("t1", "закажи билеты")
    assert task.task_id == "t1"
    assert task.input == "закажи билеты"
    assert task.state == TaskState.CREATED
    assert ag.get_task("t1") is task


def test_agent_get_task():
    """Поиск задачи по ID."""
    ag = Agent(TOOLS)
    task = ag.create_task("t1", "test")
    assert ag.get_task("t1") is task
    assert ag.get_task("nonexistent") is None


def test_agent_make_plan_order():
    """Планировщик разбивает запрос на шаги в правильном порядке."""
    ag = Agent(TOOLS)
    steps = ag._make_plan("закажи билеты в кино")
    assert len(steps) >= 2
    assert any("найти" in s for s in steps)
    assert any("подтвердить" in s for s in steps)


def test_agent_make_plan_letters():
    """Планировщик для писем."""
    ag = Agent(TOOLS)
    steps = ag._make_plan("прочти письма")
    assert any("прочитать" in s for s in steps)
    assert any("ответить" in s for s in steps)


def test_agent_select_tool_browser():
    """Выбор инструмента browser для шага с сайтом."""
    ag = Agent(TOOLS)
    tool = ag._select_tool("найти товар на сайте")
    assert tool == "browser"


def test_agent_select_tool_http():
    """Выбор инструмента http для писем."""
    ag = Agent(TOOLS)
    tool = ag._select_tool("прочитать письма")
    assert tool == "http"


def test_agent_select_tool_memory():
    """Выбор инструмента memory для подтверждения."""
    ag = Agent(TOOLS)
    tool = ag._select_tool("подтвердить заказ")
    assert tool == "memory"


def test_agent_request_user_confirmation():
    """Перевод задачи в состояние ожидания пользователя."""
    ag = Agent(TOOLS)
    task = ag.create_task("t1", "test")
    ag.request_user_confirmation("t1", "подтвердите заказ")
    assert task.state == TaskState.AWAITS_USER
    assert task.awaits_user is True


async def test_agent_run_full_cycle():
    """Полный цикл выполнения задачи."""
    ag = Agent(TOOLS)
    result = await ag.execute_task("t1", "закажи билеты")
    assert result["status"] == "done"
    assert result["steps"] > 0


async def test_agent_run_unknown_tool():
    """Ошибка при отсутствии инструмента."""
    ag = Agent({})  # пустой набор инструментов
    task = ag.create_task("t1", "закажи билеты")
    try:
        await ag.run("t1")
        assert False, "Should raise ValueError"
    except ValueError:
        pass  # ожидается


if __name__ == "__main__":
    test_agent_create_task()
    test_agent_get_task()
    test_agent_make_plan_order()
    test_agent_make_plan_letters()
    test_agent_select_tool_browser()
    test_agent_select_tool_http()
    test_agent_select_tool_memory()
    test_agent_request_user_confirmation()
    asyncio.run(test_agent_run_full_cycle())
    asyncio.run(test_agent_run_unknown_tool())
    print("All agent tests passed")
