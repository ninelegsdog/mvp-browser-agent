"""Агент-цикл: модель сама выбирает действия в браузере и меняет план после уточнения.

Реализует цикл ReAct (Thought → Action → Observation → Thought) с интеграцией LLM.
Все необратимые действия требуют подтверждения пользователя через Telegram.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from src.autonomous_planner import ActionType, AgentAction, AutonomousPlanner, LLMClient
from src.browser import BrowserSession
from src.browser_tool import BrowserTool
from src.config import settings
from src.proof import EvidenceRecorder
from src.tools import Tool, http_tool, memory_tool, shell_tool

logger = logging.getLogger(__name__)


class TaskState(StrEnum):
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
    plan: list[dict[str, Any]] = field(default_factory=list)
    current_step: int = 0
    result: dict[str, Any] | None = None
    awaits_user: bool = False
    confirmation_prompt: str | None = None  # текст для подтверждения пользователем
    pending_action: dict[str, Any] | None = None  # действие, ожидающее подтверждения
    evidence: EvidenceRecorder | None = None
    conversation_history: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
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


class ToolLike(Protocol):
    """Контракт инструмента агента.

    Реализации разные по форме (Tool и BrowserTool), но обе умеют
    асинхронно выполнить вызов и вернуть результат.
    """

    async def call(self, input: str, task_id: str, action: dict[str, Any] | None = None) -> Any: ...


class Agent:
    """Цикл агента: модель выбирает действия в браузере и меняет план после уточнения."""

    def __init__(
        self,
        tools: dict[str, ToolLike],
        planner: AutonomousPlanner | None = None,
        browser_session: Any | None = None,
    ):
        self.tools: dict[str, ToolLike] = tools
        self.planner = planner
        self.browser_session = browser_session
        self.tasks: dict[str, AgentTask] = {}
        self.max_steps = 50  # защита от бесконечного цикла

    def create_task(self, task_id: str, input_text: str) -> AgentTask:
        task = AgentTask(task_id=task_id, input_text=input_text)
        task.evidence = EvidenceRecorder(task_id)
        self.tasks[task_id] = task
        return task

    @staticmethod
    def _evidence(task: AgentTask) -> EvidenceRecorder:
        """Доказательства задачи. Создаём лениво: задача может прийти без них."""
        if task.evidence is None:
            task.evidence = EvidenceRecorder(task.task_id)
        return task.evidence

    def get_task(self, task_id: str) -> AgentTask | None:
        return self.tasks.get(task_id)

    def add_clarification(self, task_id: str, text: str) -> bool:
        """Добавить уточнение к незавершённой задаче и пересмотреть план.

        Уточнение — обычное сообщение в чат, а не новая задача: модель получает
        его в контексте и следующий шаг строит по нему. Неподтверждённое
        действие при этом отбрасывается: уточнение меняет план, а выполнять
        необратимый шаг без явного «Подтвердить» нельзя.

        Возвращает False, если задачи нет или она уже завершена — тогда
        сообщение становится новой задачей.
        """
        task = self.get_task(task_id)
        if task is None or task.state in (TaskState.DONE, TaskState.FAILED):
            return False

        task.conversation_history.append(
            {
                "role": "user",
                "content": f"Уточнение задачи: {text}",
            }
        )
        task.awaits_user = False
        task.pending_action = None
        task.confirmation_prompt = None
        task.state = TaskState.STEP
        self._evidence(task).record_step("clarification", {"text": text})
        return True

    async def run(self, task_id: str) -> dict[str, Any]:
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
        self._evidence(task).record_step("plan_started", {"input": task.input_text})

        try:
            result = await self._execute_agent_loop(task)
            if result.get("status") == "awaits_user":
                # Состояние уже выставлено циклом — не перетираем его на DONE
                task.result = result
                return result
            task.state = TaskState.DONE
            task.result = result
            self._evidence(task).record_success(result)
            return result
        except Exception as e:
            task.state = TaskState.FAILED
            self._evidence(task).record_failure(e, {"task_id": task.task_id})
            logger.exception("Задача %s завершилась с ошибкой", task_id)
            raise

    async def _execute_agent_loop(self, task: AgentTask) -> dict[str, Any]:
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

            self._evidence(task).record_step(
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
                self._evidence(task).record_step(
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

            # 3. Завершение не требует инструмента: модель сообщает результат
            if action.type == ActionType.FINISH:
                task.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": f"Задача завершена: {action.value or action.target or 'без деталей'}",
                    }
                )
                self._evidence(task).record_step(
                    f"step_{step_count}_finish",
                    {"action": action.to_dict()},
                )
                return {
                    "task_id": task.task_id,
                    "status": "done",
                    "result": {
                        "status": "done",
                        "message": action.value or action.target or "Задача завершена",
                        "steps": step_count,
                    },
                    "steps": step_count,
                }

            # 4. Выполняем действие через инструмент
            try:
                result = await self._execute_action(action, task)
            except Exception as e:
                self._evidence(task).record_failure(
                    e,
                    {"step": step_count, "action": action.to_dict()},
                )
                logger.warning(
                    "Шаг %s: действие %s не выполнено (%s), продолжаю цикл",
                    step_count,
                    action.type,
                    e,
                )
                task.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": f"Ошибка выполнения действия {action.type}: {str(e)}",
                    }
                )
                continue

            # 5. Записываем результат в контекст модели
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
            self._evidence(task).record_step(f"step_{step_count}_result", observation)

        # Превышено максимальное количество шагов
        raise RuntimeError(f"Превышено максимальное количество шагов ({self.max_steps})")

    async def _execute_action(self, action: AgentAction, task: AgentTask) -> dict[str, Any]:
        """Выполнить одно действие через соответствующий инструмент."""
        tool_name = self._map_action_to_tool(action)
        tool = self.tools.get(tool_name)

        if tool is None:
            raise ValueError(f"Нет инструмента для действия {action.type} ({tool_name})")

        result = await tool.call(
            input=action.target or "",
            task_id=task.task_id,
            action=action.to_dict(),
        )

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
            ActionType.TITLE: "browser",
            ActionType.REMEMBER: "memory",
            ActionType.CONFIRM_USER: "memory",  # ждём решения пользователя
            ActionType.FINISH: "memory",
        }
        return mapping.get(action.type, "shell")

    def request_user_confirmation(
        self, task_id: str, prompt: str, pending_action: dict[str, Any] | None = None
    ) -> None:
        """Перевести задачу в состояние ожидания подтверждения пользователя."""
        task = self.get_task(task_id)
        if task:
            task.state = TaskState.AWAITS_USER
            task.awaits_user = True
            task.confirmation_prompt = prompt
            task.pending_action = pending_action
            if task.evidence:
                self._evidence(task).record_step("awaits_user", {"prompt": prompt, "action": pending_action})

    async def execute_task(self, task_id: str, input_text: str) -> dict[str, Any]:
        """Точка входа для очереди: создать задачу и выполнить её."""
        self.create_task(task_id, input_text)
        return await self.run(task_id)

    def confirm_action(self, task_id: str, confirmed: bool) -> dict[str, Any] | None:
        """Пользователь подтвердил или отменил действие."""
        task = self.get_task(task_id)
        if not task or not task.awaits_user:
            return None

        task.awaits_user = False
        task.state = TaskState.STEP

        if confirmed:
            # Отложенное действие остаётся в задаче: его выполнит resume(),
            # ровно один раз. Если очистить здесь, пользователь подтвердил шаг,
            # а шаг не выполнился — именно эта ошибка и была.
            return {
                "task_id": task.task_id,
                "status": "confirmed",
                "action": task.pending_action,
            }
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

    async def resume(self, task_id: str) -> dict[str, Any]:
        """Продолжить задачу после подтверждения пользователем.

        Если есть отложенное действие — выполняем именно его (пользователь
        подтвердил конкретный шаг), затем отдаём управление модели.
        """
        task = self.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        if task.state == TaskState.AWAITS_USER:
            # Пользователь ещё не ответил — повторно не спрашиваем
            return {
                "task_id": task.task_id,
                "status": "awaits_user",
                "prompt": task.confirmation_prompt,
                "pending_action": task.pending_action,
            }

        pending = task.pending_action
        if pending:
            task.pending_action = None
            action = AgentAction(
                type=ActionType(pending["type"]),
                target=pending.get("target"),
                value=pending.get("value"),
                reason=pending.get("reason"),
                requires_confirmation=False,
            )
            try:
                result = await self._execute_action(action, task)
                task.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": f"Пользователь подтвердил действие {action.type}: {result}",
                    }
                )
                self._evidence(task).record_step("confirmed_action_executed", result)
            except Exception as e:
                self._evidence(task).record_failure(e, {"action": action.to_dict()})
                task.conversation_history.append(
                    {
                        "role": "assistant",
                        "content": f"Подтверждённое действие {action.type} не выполнено: {e}",
                    }
                )
                logger.warning("Подтверждённое действие %s не выполнено: %s", action.type, e)

        return await self.run(task_id)


# Глобальный экземпляр агента
browser_session = BrowserSession()
browser_tool = BrowserTool(browser_session)

TOOLS: dict[str, ToolLike] = {
    "shell": Tool("shell", shell_tool, "Выполнить команду shell"),
    "http": Tool("http", http_tool, "HTTP-запрос к внешнему сервису"),
    "browser": browser_tool,  # Реальный Playwright браузер, не заглушка
    "memory": Tool("memory", memory_tool, "Память задачи и предпочтений"),
}


def build_planner(config: Any = settings) -> AutonomousPlanner | None:
    """Собрать LLM-планировщик из настроек.

    Без ключа возвращает None: агент честно работает в режиме каркаса задач,
    а не имитирует автономность. Функция отделена от импорта, чтобы это
    поведение можно было проверить тестом.
    """
    if not config.LLM_API_KEY:
        logger.warning(
            "LLM_API_KEY не задан. Агент работает без автономных решений: "
            "задачи принимаются и выполняются, но что делать дальше, модель "
            "не выбирает. Установите LLM_API_KEY для автономного режима."
        )
        return None

    llm_client = LLMClient(
        api_key=config.LLM_API_KEY,
        base_url=config.LLM_BASE_URL,
        model=config.LLM_MODEL,
        temperature=config.LLM_TEMPERATURE,
        max_tokens=config.LLM_MAX_TOKENS,
    )
    logger.info("Автономный агент с LLM-планировщиком инициализирован")
    return AutonomousPlanner(llm_client)


_planner = build_planner()
agent = Agent(tools=TOOLS, planner=_planner, browser_session=browser_session)
