"""Абстрактный базовый класс для очереди задач.

Позволяет заменять реализацию очереди (Redis, в памяти, и т.д.)
без изменения кода агента и других компонентов.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """Единица работы в очереди."""
    task_id: str
    input: str
    created_at: str = ""
    retries: int = 0
    priority: str = "med"
    type: str = "echo"
    command: Optional[str] = None
    path: Optional[str] = None
    idempotency_key: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "task_id": self.task_id,
            "input": self.input,
            "created_at": self.created_at,
            "retries": self.retries,
            "priority": self.priority,
            "type": self.type,
        }
        if self.command:
            d["command"] = self.command
        if self.path:
            d["path"] = self.path
        if self.idempotency_key:
            d["idempotency_key"] = self.idempotency_key
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Task":
        return cls(
            task_id=d["task_id"],
            input=d["input"],
            created_at=d.get("created_at", ""),
            retries=d.get("retries", 0),
            priority=d.get("priority", "med"),
            type=d.get("type", "echo"),
            command=d.get("command"),
            path=d.get("path"),
            idempotency_key=d.get("idempotency_key"),
            extra={k: v for k, v in d.items() if k not in
                   {"task_id", "input", "created_at", "retries", "priority", "type", "command", "path", "idempotency_key"}},
        )


class TaskQueueInterface:
    """Абстрактный интерфейс очереди задач.

    Реализации могут использовать Redis, RabbitMQ, в памяти и т.д.
    """

    async def connect(self) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def enqueue(self, task: Task) -> str:
        """Добавить задачу в очередь. Возвращает task_id."""
        raise NotImplementedError

    async def pop(self) -> Optional[Task]:
        """Извлечь задачу из очереди. Блокирующая операция с таймаутом."""
        raise NotImplementedError

    async def save_result(self, task_id: str, result: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def fail(self, task: Task, error: Exception) -> None:
        """Обработать сбой задачи: повтор или DLQ."""
        raise NotImplementedError

    async def process(self, task: Task) -> None:
        """Выполнить задачу и сохранить результат."""
        raise NotImplementedError

    async def worker(self) -> None:
        """Запустить бесконечный цикл обработки задач."""
        raise NotImplementedError


class InMemoryTaskQueue(TaskQueueInterface):
    """Реализация очереди в памяти для тестов и разработки."""

    def __init__(self):
        self._queues: Dict[str, asyncio.Queue] = {
            "high": asyncio.Queue(),
            "med": asyncio.Queue(),
            "low": asyncio.Queue(),
        }
        self._results: Dict[str, Dict[str, Any]] = {}
        self._dlq: List[Dict[str, Any]] = []
        self._shutdown = False

    async def connect(self) -> None:
        pass

    async def close(self) -> None:
        self._shutdown = True

    async def enqueue(self, task: Task) -> str:
        if not task.created_at:
            task.created_at = datetime.now(timezone.utc).isoformat()
        queue = self._queues.get(task.priority, self._queues["med"])
        await queue.put(task)
        logger.info("Enqueued task %s to %s", task.task_id, task.priority)
        return task.task_id

    async def pop(self) -> Optional[Task]:
        for queue_name in ["high", "med", "low"]:
            queue = self._queues[queue_name]
            try:
                return queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
        return None

    async def save_result(self, task_id: str, result: Dict[str, Any]) -> None:
        self._results[task_id] = result

    async def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._results.get(task_id)

    async def fail(self, task: Task, error: Exception) -> None:
        retries = task.retries
        if retries < 2:
            task.retries = retries + 1
            await self.enqueue(task)
            logger.warning("Task %s failed (retry %d/2): %s", task.task_id, task.retries, error)
        else:
            self._dlq.append({"task": task.to_dict(), "error": str(error)})
            logger.error("Task %s moved to DLQ: %s", task.task_id, error)

    async def process(self, task: Task) -> None:
        raise NotImplementedError("Use subclass with execute logic")

    async def worker(self) -> None:
        while not self._shutdown:
            task = await self.pop()
            if task is None:
                await asyncio.sleep(0.1)
                continue
            await self.process(task)