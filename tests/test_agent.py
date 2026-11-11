import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.agent import Agent, TaskState, Tool


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
    """Без планировщика агент честно сообщает, что автономный режим выключен."""
    ag = Agent(tools={})
    result = await ag.execute_task("t1", "закажи билеты")
    assert result["status"] == "done"
    assert result["steps"] == 1
    assert "без LLM-планировщика" in result["result"]["message"]


class _FakePlanner:
    """Планировщик-двойник: отдаёт заранее заданную последовательность действий."""

    def __init__(self, actions):
        self._actions = list(actions)
        self.calls = 0

    async def decide_next_action(self, task_description, conversation_history, available_tools):
        self.calls += 1
        if not self._actions:
            raise AssertionError("Планировщик вызван больше раз, чем задано действий")
        return self._actions.pop(0)


async def test_react_loop_executes_actions_and_finishes():
    """ReAct-цикл реально вызывает инструмент по действию и завершается по FINISH."""
    from src.autonomous_planner import ActionType, AgentAction

    executed = []

    async def fake_memory(input: str, task_id: str, action=None) -> str:
        executed.append((input, task_id, action))
        return "ok"

    remember = AgentAction(type=ActionType.REMEMBER, target="note=1")
    finish = AgentAction(type=ActionType.FINISH, target="itog", value="готово")
    ag = Agent(tools={"memory": Tool("memory", fake_memory, "память")})
    ag.planner = _FakePlanner([remember, finish])
    task = ag.create_task("t1", "запомни")

    result = await ag.run("t1")
    assert result["status"] == "done"
    assert result["steps"] == 2
    assert executed == [("note=1", "t1", remember.to_dict())]
    assert task.state == TaskState.DONE


async def test_react_loop_stops_for_confirmation():
    """Необратимое действие переводит задачу в ожидание пользователя, не выполняясь."""
    from src.autonomous_planner import ActionType, AgentAction

    calls = []

    async def fake_memory(input: str, task_id: str, action=None) -> str:
        calls.append(input)
        return "ok"

    ag = Agent(tools={"memory": Tool("memory", fake_memory, "память")})
    ag.planner = _FakePlanner(
        [
            AgentAction(
                type=ActionType.FINISH,
                target="Оформить заказ на 5000 руб?",
                requires_confirmation=True,
            )
        ]
    )
    task = ag.create_task("t1", "купи")
    result = await ag.run("t1")

    assert result["status"] == "awaits_user"
    assert result["prompt"] == "Оформить заказ на 5000 руб?"
    assert task.state == TaskState.AWAITS_USER
    assert calls == []  # действие НЕ выполнено до подтверждения


async def test_react_loop_unknown_tool_does_not_crash_silently():
    """Отсутствующий инструмент логируется и цикл продолжается, а не падает молча."""
    from src.autonomous_planner import ActionType, AgentAction

    ag = Agent(tools={})  # нет ни одного инструмента
    planner = _FakePlanner([])
    ag.planner = planner
    ag.create_task("t1", "сделай")

    # browser-действие при пустом наборе инструментов -> ошибка -> цикл идёт дальше
    planner._actions = [
        AgentAction(type=ActionType.SCREENSHOT, target=""),
        AgentAction(type=ActionType.FINISH, target="готово"),
    ]
    result = await ag.run("t1")
    assert result["status"] == "done"
    assert result["steps"] == 2
    assert result["result"]["message"] == "готово"


async def test_confirm_then_resume_executes_confirmed_action():
    """Путь бота: кнопка «Подтвердить» → resume выполняет именно это действие.

    Проверка на стыке двух функций: confirm_action не имеет права выбросить
    отложенное действие, иначе пользователь подтвердил шаг, а он не выполнился.
    """
    from src.autonomous_planner import ActionType, AgentAction

    calls = []

    async def fake_memory(input: str, task_id: str, action=None) -> str:
        calls.append(input)
        return "ok"

    ag = Agent(tools={"memory": Tool("memory", fake_memory, "память")})
    ag.planner = _FakePlanner([AgentAction(type=ActionType.FINISH, target="готово", value="сделано")])
    task = ag.create_task("t1", "оформи заказ")
    task.state = TaskState.AWAITS_USER
    task.awaits_user = True
    task.confirmation_prompt = "Подтвердите сохранение заказа"
    task.pending_action = {
        "type": ActionType.REMEMBER.value,
        "target": "order=5000",
        "value": None,
        "reason": "сохраняем заказ",
    }

    # Так это делает бот: нажатие «Подтвердить», возвращённое значение не используется
    ag.confirm_action("t1", confirmed=True)
    result = await ag.resume("t1")

    assert calls == ["order=5000"]
    assert result["status"] == "done"
    assert task.pending_action is None


async def test_cancelled_action_is_not_executed_on_resume():
    """После «Отменить» отложенное действие не выполняется."""
    from src.autonomous_planner import ActionType, AgentAction

    calls = []

    async def fake_memory(input: str, task_id: str, action=None) -> str:
        calls.append(input)
        return "ok"

    ag = Agent(tools={"memory": Tool("memory", fake_memory, "память")})
    ag.planner = _FakePlanner([AgentAction(type=ActionType.FINISH, target="готово")])
    task = ag.create_task("t1", "оформи заказ")
    task.state = TaskState.AWAITS_USER
    task.awaits_user = True
    task.pending_action = {"type": ActionType.REMEMBER.value, "target": "order=1"}

    ag.confirm_action("t1", confirmed=False)
    await ag.resume("t1")

    assert calls == []
    assert task.pending_action is None


async def test_resume_executes_confirmed_action_once():
    """После «Подтвердить» выполняется именно отложенное действие, затем цикл идёт дальше."""
    from src.autonomous_planner import ActionType, AgentAction

    calls = []

    async def fake_memory(input: str, task_id: str, action=None) -> str:
        calls.append(input)
        return "ok"

    finish = AgentAction(type=ActionType.FINISH, target="готово", value="заказ оформлен")
    ag = Agent(tools={"memory": Tool("memory", fake_memory, "память")})
    ag.planner = _FakePlanner([finish])

    task = ag.create_task("t1", "оформи заказ")
    task.state = TaskState.STEP
    task.awaits_user = False
    task.pending_action = {
        "type": ActionType.REMEMBER.value,
        "target": "order=5000",
        "value": None,
        "reason": "сохраняем заказ",
        "requires_confirmation": True,
    }

    result = await ag.resume("t1")

    assert calls == ["order=5000"]  # подтверждённое действие выполнено ровно один раз
    assert result["status"] == "done"
    assert task.pending_action is None


async def test_resume_waits_if_user_has_not_confirmed():
    """Пока пользователь не ответил, resume не выполняет отложенное действие."""
    from src.autonomous_planner import ActionType

    calls = []

    async def fake_memory(input: str, task_id: str, action=None) -> str:
        calls.append(input)
        return "ok"

    ag = Agent(tools={"memory": Tool("memory", fake_memory, "память")})
    ag.planner = _FakePlanner([])
    task = ag.create_task("t1", "оформи заказ")
    task.state = TaskState.AWAITS_USER
    task.awaits_user = True
    task.confirmation_prompt = "Подтвердите оплату"
    task.pending_action = {"type": ActionType.REMEMBER.value, "target": "order=1"}

    result = await ag.resume("t1")

    assert result["status"] == "awaits_user"
    assert calls == []
    assert task.pending_action is not None


if __name__ == "__main__":
    test_agent_create_task()
    test_agent_get_task()
    test_agent_request_user_confirmation()
    test_agent_confirm_action()
    test_agent_confirm_action_rejected()
    asyncio.run(test_agent_run_full_cycle())
    print("Все тесты агента пройдены")


def test_build_planner_returns_none_without_api_key():
    """Без ключа LLM агент не притворяется автономным."""
    from types import SimpleNamespace

    from src.agent import build_planner

    config = SimpleNamespace(
        LLM_API_KEY="",
        LLM_BASE_URL="https://api.example.com/v1",
        LLM_MODEL="model",
        LLM_TEMPERATURE=0.1,
        LLM_MAX_TOKENS=100,
    )
    assert build_planner(config) is None


def test_build_planner_wires_settings_into_llm_client():
    """С ключом создаётся реальный планировщик, и настройки доходят до клиента."""
    from types import SimpleNamespace

    from src.agent import build_planner
    from src.autonomous_planner import AutonomousPlanner, LLMClient

    config = SimpleNamespace(
        LLM_API_KEY="key-123",
        LLM_BASE_URL="https://api.example.com/v1",
        LLM_MODEL="gpt-4o-mini",
        LLM_TEMPERATURE=0.25,
        LLM_MAX_TOKENS=512,
    )
    planner = build_planner(config)

    assert isinstance(planner, AutonomousPlanner)
    client = planner.client
    assert isinstance(client, LLMClient)
    assert client.api_key == "key-123"
    assert client.base_url == "https://api.example.com/v1"
    assert client.model == "gpt-4o-mini"
    assert client.temperature == 0.25
    assert client.max_tokens == 512
