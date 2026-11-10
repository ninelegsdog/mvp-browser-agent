"""Агент-цикл: модель выбирает инструменты, не действия в браузере.

Инструменты: shell, HTTP, файлы, память. Браузерная часть работает по сценарию
(src/browser.py), а не выбирается моделью автономно.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.browser import BrowserSession
from src.browser_tool import BrowserTool
from src.config import settings
from src.proof import EvidenceRecorder
from src.security import assert_no_secrets_in_text

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
    input: str
    state: TaskState = TaskState.CREATED
    plan: List[str] = field(default_factory=list)
    current_step: int = 0
    result: Optional[Dict[str, Any]] = None
    awaits_user: bool = False
    evidence: Optional[EvidenceRecorder] = None


class Tool:
    """Базовый инструмент агента. Все инструменты синхронные/асинхронные функции."""

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
    """Цикл агента: выбирает инструмент, выполняет, перестраивает план после результата."""

    def __init__(self, tools: Dict[str, Tool]):
        self.tools = tools
        self.tasks: Dict[str, AgentTask] = {}

    def create_task(self, task_id: str, input_text: str) -> AgentTask:
        task = AgentTask(task_id=task_id, input=input_text)
        task.evidence = EvidenceRecorder(task_id)
        self.tasks[task_id] = task
        return task

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        return self.tasks.get(task_id)

    async def run(self, task_id: str) -> Dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        task.state = TaskState.PLAN
        task.evidence.record_step("plan_started", {"input": task.input})

        # План: упрощённый — разбор входного текста на шаги
        task.plan = self._make_plan(task.input)
        task.state = TaskState.STEP
        task.evidence.record_step("plan_made", {"steps": task.plan})

        for i, step in enumerate(task.plan):
            task.current_step = i
            task.evidence.record_step(f"step_{i}", {"step": step})

            # Агент выбирает инструмент для шага (не действие в браузере)
            tool_name = self._select_tool(step)
            tool = self.tools.get(tool_name)
            if not tool:
                raise ValueError(f"No tool {tool_name} for step: {step}")

            try:
                result = await tool.call(input=step, task_id=task_id)
                task.evidence.record_step(f"step_{i}_done", {"tool": tool_name, "result": str(result)[:200]})
            except Exception as e:
                task.state = TaskState.FAILED
                task.evidence.record_failure(e, {"step": i, "step_text": step})
                raise

        task.state = TaskState.DONE
        task.result = {"status": "done", "steps": len(task.plan)}
        task.evidence.record_success(task.result)
        return task.result

    def _make_plan(self, input_text: str) -> List[str]:
        """Упрощённый планировщик: разбивает на шаги по ключевым словам."""
        steps = []
        text = input_text.lower()
        if "оформ" in text or "заказ" in text:
            steps.append("найти товар на сайте")
            steps.append("подтвердить заказ с пользователем")
            steps.append("оформить заказ после подтверждения")
        elif "письм" in text:
            steps.append("прочитать письма")
            steps.append("ответить на важные")
        elif "билет" in text:
            steps.append("найти билеты")
            steps.append("подтвердить покупку с пользователем")
        else:
            steps.append("обработать запрос: " + input_text[:50])
        return steps

    def _select_tool(self, step: str) -> str:
        """Выбор инструмента для шага. Не выбор действия в браузере."""
        if "сайт" in step or "товар" in step or "билет" in step:
            return "browser"
        if "письм" in step:
            return "http"
        if "подтвердить" in step:
            return "memory"  # ждёт подтверждение пользователя в памяти задачи
        return "shell"

    def request_user_confirmation(self, task_id: str, prompt: str) -> None:
        """Переводит задачу в состояние «ждёт вас» — нужен ввод пользователя."""
        task = self.get_task(task_id)
        if task:
            task.state = TaskState.AWAITS_USER
            task.awaits_user = True
            if task.evidence:
                task.evidence.record_step("awaits_user", {"prompt": prompt})

    async def execute_task(self, task_id: str, input_text: str) -> Dict[str, Any]:
        """Точка входа для очереди: создать задачу и выполнить её.

        Возвращает результат выполнения. Используется из queue.py вместо
        заглушки execute().
        """
        task = self.create_task(task_id, input_text)
        result = await self.run(task_id)
        return result


# ---------------------------------------------------------------------------
# Инструменты (заглушки для shell/http/memory, браузер — реальный Playwright)
# ---------------------------------------------------------------------------

async def shell_tool(input: str, task_id: str) -> str:
    """Выполнить безопасную команду shell (только чтение в демо)."""
    await asyncio.sleep(0.2)
    return f"shell executed: {input[:50]}"


async def http_tool(input: str, task_id: str) -> str:
    """HTTP-запрос (в проде — с таймаутом и ретраями)."""
    await asyncio.sleep(0.3)
    return f"http response for: {input[:50]}"


async def memory_tool(input: str, task_id: str) -> str:
    """Чтение/запись в памяти задачи (состояние, предпочтения)."""
    await asyncio.sleep(0.1)
    return f"memory: {input[:50]}"


# Сессия браузера с постоянным профилем (запускается при первом использовании)
browser_session = BrowserSession()
browser_tool = BrowserTool(browser_session)

TOOLS = {
    "shell": Tool("shell", shell_tool, "Выполнить команду shell"),
    "http": Tool("http", http_tool, "HTTP-запрос к внешнему сервису"),
    "browser": browser_tool,  # Реальный Playwright браузер, не заглушка
    "memory": Tool("memory", memory_tool, "Память задачи и предпочтений"),
}

agent = Agent(TOOLS)
