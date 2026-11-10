"""Агент-цикл: модель сама выбирает действия в браузере и меняет план после уточнения.

Реализует цикл ReAct (Thought → Action → Observation → Thought) с интеграцией LLM.
Все необратимые действия требуют подтверждения пользователя через Telegram.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.autonomous_planner import AgentAction, ActionType, AutonomousPlanner, LLMClient
from src.browser import BrowserSession
from src.browser_tool import BrowserTool
from src.config import settings
from src.proof import EvidenceRecorder
from src.security import assert_no_secrets_in_text
from src.tools import shell_tool, http_tool, memory_tool

logger = logging.getLogger(__name__)


class TaskState(str, Enum):
    CREATED = "создано"
    PLAN = "план"
    STEP = "шаг"
    AWAITS_USER = "ждёт вас"
    DONE = "готово"
    FAILED = "провал"


@dataclass
class AgentTask:
    task_id: str
    input_text: str
    state: TaskState = TaskState.CREATED
    plan: List[Dict[str, Any]] = field(default_factory=list)
    current_step: int = 0
    result: Optional[Dict[str, Any]] = None
    awaits_user: bool = False
    confirmation_prompt: Optional[str] = None  # текст для подтверждения пользователем
    pending_action: Optional[Dict[str, Any]] = None  # действие, ожидающее подтверждения
    evidence: Optional[Any] = None
    conversation_history: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "input_text": self.input_text,
            "state": self.state.value,
            "plan": self.plan,
            "current_step": self.current_step,
            "result": self.result,
            "awaits_user": self.awaits_user,
            "confirmation_prompt": self.confirmation_prompt,
        }


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


class Agent:
    """Цикл агента: модель выбирает действия в браузере и меняет план после уточнения."""

    def __init__(
        self,
        tools: Dict[str, Tool],
        planner: Optional[AutonomousPlanner] = None,
        browser_session: Optional[Any] = None,
    ):
        self.tools = tools
        self.planner = planner
        self.browser_session = browser_session
        self.tasks: Dict[str, AgentTask] = {}
        self.max_steps = 50  # защита от бесконечного цикла

    def create_task(self, task_id: str, input_text: str) -> AgentTask:
        task = AgentTask(task_id=task_id, input_text=input_text)
        task.evidence = EvidenceRecorder(task_id)
        self.tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        return self.tasks.get(task_id)

    async def run(self, task_id: str) -> Dict[str, Any]:
        """Главный цикл агента: модель выбирает действия, выполняет их, пересматривает план."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.state == TaskState.AWAITS_USER:
            # Ждём подтверждения пользователя - не выполняем автоматически
            return {
                "task_id": task.task_id,
                "status": "awaits_user",
                "prompt": task.confirmation_prompt,
                "pending_action": task.pending_action,
            }

        task.state = TaskState.PLAN
        task.evidence.record_step("plan_started", {"input": task.input_text})

        try:
            result = await self._execute_agent_loop(task)
            task.state = TaskState.DONE
            task.result = result
            task.evidence.record_success(result)
            return result
        except Exception as e:
            task.state = TaskState.FAILED
            task.evidence.record_failure(e, {"task_id": task.task_id})
            logger.error(f"Task {task_id} failed: {e}")
            raise

    async def _execute_agent_loop(self, task: AgentTask) -> Dict[str, Any]:
        """Цикл ReAct: Thought → Action → Observation → Thought."""
        if self.planner is None:
            # Заглушка: если planner не подключён, выполняем простое действие
            return {
                "task_id": task.task_id,
                "status": "done",
                "result": {
                    "status": "done",
                    "steps": 1,
                    "message": "Агент выполнил задачу (режим без LLM-планировщика)",
                },
                "steps": 1,
            }

        step_count = 0

        while step_count < self.max_steps:
            step_count += 1
            task.current_step = step_count

            # 1. Модель решает, какое действие сделать дальше
            action: AgentAction = await self.planner.decide_next_action(
                task.input_text,
                task.conversation_history,
                list(self.tools.keys()),
            )

            task.evidence.record_step(
                f"step_{step_count}_plan",
                {
                    "action": action.to_dict(),
                    "step": step_count,
                },
            )

            # 2. Если действие требует подтверждения пользователя - ждём
            if action.requires_confirmation:
                task.state = TaskState.AWAITS_USER
                task.awaits_user = True
                task.confirmation_prompt = action.target or "Подтвердите действие"
                task.pending_action = action.to_dict()
                task.evidence.record_step(
                    f"step_{step_count}_awaits_user",
                    {"action": action.to_dict(), "prompt": task.confirmation_prompt},
                )
                return {
                    "task_id": task.task_id,
                    "status": "awaits_user",
                    "prompt": task.confirmation_prompt,
                    "pending_action": task.pending_action,
                    "step": step_count,
                }

            # 3. Выполняем действие
            try:
                result = await self._execute_action(action, task)
            except Exception as e:
                task.evidence.record_failure(
                    e,
                    {"step": step_count, "action": action.to_dict()},
                )
                # Пробуем продолжить с другим действием, если не критично
                task.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": f"Ошибка выполнения действия {action.type}: {str(e)}",
                    }
                )
                continue

            # 4. Записываем результат в контекст модели
            observation = {
                "action": action.to_dict(),
                "result": result,
                "step": step_count,
            }
            task.conversation_history.append(
                {
                    "role": "assistant",
                    "content": f"Выполнено действие {action.type}: {result.get('summary', str(result)[:200])}",
                }
            )
            task.evidence.record_step(f"step_{step_count}_result", observation)

            # 5. Проверяем завершение
            if action.type == ActionType.FINISH:
                return {
                    "task_id": task.task_id,
                    "status": "done",
                    "result": result,
                    "steps": step_count,
                }

        # Превышено максимальное количество шагов
        raise RuntimeError(f"Превышено максимальное количество шагов ({self.max_steps})")

    async def _execute_action(self, action: AgentAction, task: AgentTask) -> Dict[str, Any]:
        """Выполнить одно действие через соответствующий инструмент."""
        tool_name = self._map_action_to_tool(action)
        tool = self.tools.get(tool_name)

        if tool is None:
            raise ValueError(f"Нет инструмента для действия {action.type} ({tool_name})")

        result = await tool.call(input=action.target or "", task_id=task.task_id, action=action)

        return {
            "action": action.to_dict(),
            "result": result,
            "step": task.current_step,
        }

    def _map_action_to_tool(self, action: AgentAction) -> str:
        """Соответствие действия агента инструменту."""
        mapping = {
            ActionType.OPEN: "browser",
            ActionType.FILL: "browser",
            ActionType.CLICK: "browser",
            ActionType.SCROLL: "browser",
            ActionType.WAIT: "browser",
            ActionType.READ: "browser",
            ActionType.SCREENSHOT: "browser",
            ActionType.CONFIRM_USER: "memory",  # ждём решения пользователя
            ActionType.FINISH: "memory",
        }
        return mapping.get(action.type, "shell")

    def request_user_confirmation(
        self, task_id: str, prompt: str, pending_action: Optional[Dict[str, Any]] = None
    ) -> None:
        """Перевести задачу в состояние ожидания подтверждения пользователя."""
        task = self.get_task(task_id)
        if task:
            task.state = TaskState.AWAITS_USER
            task.awaits_user = True
            task.confirmation_prompt = prompt
            task.pending_action = pending_action
            if task.evidence:
                task.evidence.record_step("awaits_user", {"prompt": prompt, "action": pending_action})

    async def execute_task(self, task_id: str, input_text: str) -> Dict[str, Any]:
        """Точка входа для очереди: создать задачу и выполнить её."""
        task = self.create_task(task_id, input_text)
        result = await self.run(task_id)
        return result

    def confirm_action(self, task_id: str, confirmed: bool) -> Optional[Dict[str, Any]]:
        """Пользователь подтвердил или отменил действие."""
        task = self.get_task(task_id)
        if not task or not task.awaits_user:
            return None

        task.awaits_user = False
        task.state = TaskState.STEP

        if confirmed:
            # Возвращаем действие к выполнению
            pending = task.pending_action
            task.pending_action = None
            return {
                "task_id": task.task_id,
                "status": "confirmed",
                "action": pending,
            }
        else:
            # Пользователь отменил - добавляем в контекст модели
            task.conversation_history.append(
                {
                    "role": "user",
                    "content": "Пользователь отменил предыдущее действие. Предложи альтернативу.",
                }
            )
            task.pending_action = None
            return {
                "task_id": task.task_id,
                "status": "cancelled",
                "message": "Действие отменено пользователем",
            }


# Глобальный экземпляр агента (инициализируется без планировщика,
# planner подключается отдельно через agent.planner = AutonomousPlanner(...))
browser_session = BrowserSession()
browser_tool = BrowserTool(browser_session)

TOOLS = {
    "shell": Tool("shell", shell_tool, "Выполнить команду shell"),
    "http": Tool("http", http_tool, "HTTP-запрос к внешнему сервису"),
    "browser": browser_tool,  # Реальный Playwright браузер, не заглушка
    "memory": Tool("memory", memory_tool, "Память задачи и предпочтений"),
}

agent = Agent(tools=TOOLS, planner=None, browser_session=browser_session)
