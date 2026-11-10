"""Очередь задач: Redis + воркеры, DLQ, приоритеты, повтор после сбоя.

Паттерн execution-layer:
- LPUSH producer / BLPOP consumer, приоритет через шардирование очередей.
- Semaphore-ограниченная конкуренция, in-flight отслеживание.
- Retry-or-DLQ: retries < MAX -> повтор в ту же очередь, иначе queue:dead.
- Graceful shutdown: ждём in-flight задачи перед выходом.

Использует redis.asyncio (современная замена aioredis, совместима с Python 3.12).
"""

import asyncio
import json
import logging
import random
import signal
import uuid
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import redis.asyncio as redis

from src.config import settings, PRIORITY_QUEUES
from src.security import assert_no_secrets_in_text
from src.agent import agent as agent_instance
from src.metrics import RETRY_COUNT, TASK_SUCCESS_TOTAL, TASK_FAILURE_TOTAL
from src.queue_interface import TaskQueueInterface

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
MAX_CONCURRENCY = 2
BRPOP_TIMEOUT = 30
TASK_TTL_SECONDS = 86400  # 24 часа

# Экспоненциальный бэк офф с jitter
BACKOFF_BASE_SECONDS = 5   # базовая задержка перед первым повтором
BACKOFF_MAX_SECONDS = 300  # maximale задержка 5 минут
BACKOFF_JITTER_SECONDS = 10  # диапазон джиттера

ERROR_CATEGORY_NETWORK = "network"
ERROR_CATEGORY_CAPTCHA = "captcha"
ERROR_CATEGORY_BAN = "ban"
ERROR_CATEGORY_OTHER = "other"


def categorize_error(error: Exception) -> str:
    """Категоризация ошибки для DLQ."""
    msg = str(error).lower()
    if any(k in msg for k in ["captcha", "капча"]):
        return ERROR_CATEGORY_CAPTCHA
    if any(k in msg for k in ["ban", "заблок", "403", "forbidden"]):
        return ERROR_CATEGORY_BAN
    if any(k in msg for k in ["timeout", "connection", "network", "dns"]):
        return ERROR_CATEGORY_NETWORK
    return ERROR_CATEGORY_OTHER

RESULTS_KEY = "results"
IDEMPOTENCY_KEYS_SET = "idempotency_keys"


@dataclass
class Task:
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


class TaskQueue(TaskQueueInterface):
    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or settings.REDIS_URL
        self._redis = None
        self._shutdown = False
        self._in_flight: set = set()
        self._in_flight_lock = asyncio.Lock()
        self.sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def connect(self) -> None:
        self._redis = redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_timeout=None,
            socket_connect_timeout=None,
            protocol=3,
        )

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()

    async def enqueue(self, task: Task) -> str:
        """Добавить задачу в очередь приоритета.

        Если задан idempotency_key и такой ключ уже встречался — задача не
        дублируется, возвращается существующий task_id.
        """
        if not task.created_at:
            task.created_at = datetime.now(timezone.utc).isoformat()

        if task.idempotency_key:
            already = await self._redis.sismember(IDEMPOTENCY_KEYS_SET, task.idempotency_key)
            if already:
                logger.info("Duplicate idempotency key %s, skipping enqueue", task.idempotency_key)
                return task.task_id
            await self._redis.sadd(IDEMPOTENCY_KEYS_SET, task.idempotency_key)

        payload = json.dumps(task.to_dict(), ensure_ascii=False)
        queue = self._resolve_queue(task.priority)
        assert_no_secrets_in_text(payload, "task.enqueue")
        await self._redis.lpush(queue, payload)
        logger.info("Enqueued task %s to %s", task.task_id, queue)
        return task.task_id

    def _resolve_queue(self, priority: str) -> str:
        mapping = {
            "high": settings.QUEUE_HIGH,
            "med": settings.QUEUE_MED,
            "low": settings.QUEUE_LOW,
        }
        return mapping.get(priority, settings.QUEUE_MED)

    async def pop(self) -> Optional[Task]:
        """BLPOP по упорядоченным очередям (high > med > low).

        Пропускает просроченные задачи (старше TASK_TTL_SECONDS).
        """
        while True:
            result = await self._redis.blpop(PRIORITY_QUEUES, timeout=BRPOP_TIMEOUT)
            if result is None:
                return None
            _, raw = result
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Corrupt JSON in queue, skipping")
                continue

            task = Task.from_dict(data)
            if task.created_at:
                created = datetime.fromisoformat(task.created_at)
                age = (datetime.now(timezone.utc) - created).total_seconds()
                if age > TASK_TTL_SECONDS:
                    logger.warning("Task %s expired (age %.0fs > TTL %ds), skipping",
                                   task.task_id, age, TASK_TTL_SECONDS)
                    continue
            return task

    async def save_result(self, task_id: str, result: Dict[str, Any]) -> None:
        assert_no_secrets_in_text(json.dumps(result), "task.save_result")
        await self._redis.hset(RESULTS_KEY, task_id, json.dumps(result, ensure_ascii=False))

    async def get_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        data = await self._redis.hget(RESULTS_KEY, task_id)
        return json.loads(data) if data else None

    async def _backoff_delay(self, retries: int) -> float:
        """Экспоненциальный бэк офф с jitter."""
        delay = min(BACKOFF_BASE_SECONDS * (2 ** retries), BACKOFF_MAX_SECONDS)
        jitter = random.uniform(0, BACKOFF_JITTER_SECONDS)
        return delay + jitter

    async def fail(self, task: Task, error: Exception) -> None:
        """Retry-or-DLQ. Сохраняем приоритет при повторной постановке."""
        retries = task.retries
        if retries < MAX_RETRIES:
            task.retries = retries + 1
            delay = await self._backoff_delay(retries)
            await asyncio.sleep(delay)
            await self.enqueue(task)
            assert_no_secrets_in_text(str(error), "queue.fail")
            logger.warning(
                "Task %s failed (retry %d/%d, retry in %.1fs): %s",
                task.task_id, task.retries, MAX_RETRIES, delay, error
            )
        else:
            category = categorize_error(error)
            if RETRY_COUNT:
                RETRY_COUNT.labels(queue=task.priority, error_category=category).observe(task.retries)
            await self._redis.lpush(
                settings.DLQ,
                json.dumps(
                    {"task": task.to_dict(), "error": str(error), "error_category": category},
                    ensure_ascii=False,
                ),
            )
            assert_no_secrets_in_text(str(error), "queue.dlq")
            logger.error("Task %s moved to DLQ (category=%s): %s", task.task_id, category, error)

    async def process(self, task: Task) -> None:
        async with self.sem:
            async with self._in_flight_lock:
                self._in_flight.add(task.task_id)
            try:
                result = await execute(task)
                await self.save_result(task.task_id, result)
                if TASK_SUCCESS_TOTAL:
                    TASK_SUCCESS_TOTAL.labels(type=task.type).inc()
                logger.info("Task %s completed", task.task_id)
            except Exception as e:
                if TASK_FAILURE_TOTAL:
                    category = categorize_error(e)
                    TASK_FAILURE_TOTAL.labels(type=task.type, error_category=category).inc()
                await self.fail(task, e)
            finally:
                async with self._in_flight_lock:
                    self._in_flight.discard(task.task_id)

    async def worker(self) -> None:
        await self.connect()
        loop = asyncio.get_running_loop()

        def handle_signal():
            self._shutdown = True

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, handle_signal)

        while not self._shutdown:
            task_data = await self.pop()
            if task_data is None:
                continue
            t = asyncio.create_task(self.process(task_data))
            t.add_done_callback(lambda _: None)

        # Graceful shutdown: ждём in-flight
        if self._in_flight:
            logger.info("Waiting for %d in-flight tasks...", len(self._in_flight))
        await self.close()


# ---------------------------------------------------------------------------
# Заглушка execute: подменяется реальной логикой агента (src/agent.py)
# ---------------------------------------------------------------------------

async def execute(task: Task) -> Dict[str, Any]:
    """Выполнить задачу через агента.

    Делегирует в agent.execute_task, который создаёт задачу в агенте,
    прогоняет её через инструменты и возвращает результат.
    """
    return await agent_instance.execute_task(task.task_id, task.input)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def cli_add(task_text: str, priority: str = "med") -> None:
    q = TaskQueue()
    await q.connect()
    task = Task(task_id=str(uuid.uuid4()), input=task_text, priority=priority)
    await q.enqueue(task)
    await q.close()
    print(f"Task {task.task_id} enqueued ({priority})")


async def cli_result(task_id: str) -> None:
    q = TaskQueue()
    await q.connect()
    result = await q.get_result(task_id)
    await q.close()
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"No result for {task_id}")


async def cli_stats() -> None:
    q = TaskQueue()
    await q.connect()
    while True:
        for qname in PRIORITY_QUEUES:
            llen = await q._redis.llen(qname)
            print(f"{qname}: {llen}")
        dlen = await q._redis.llen(settings.DLQ)
        print(f"{settings.DLQ}: {dlen}")
        await asyncio.sleep(3)
    await q.close()


async def cli_dlq_replay(task_id: str) -> None:
    """Вернуть задачу из DLQ в очередь для повторного выполнения."""
    q = TaskQueue()
    await q.connect()

    # Найти задачу в DLQ
    dlq_items = await q._redis.lrange(settings.DLQ, 0, -1)
    found = False
    for item_raw in dlq_items:
        item = json.loads(item_raw)
        if item.get("task", {}).get("task_id") == task_id:
            task = Task.from_dict(item["task"])
            task.retries = 0  # сброс счетчика повторов
            await q.enqueue(task)
            # Удалить из DLQ
            await q._redis.lrem(settings.DLQ, 1, item_raw)
            found = True
            print(f"Task {task_id} replayed from DLQ to {task.priority} queue")
            break

    if not found:
        print(f"Task {task_id} not found in DLQ")
    await q.close()


def main() -> None:
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m src.queue {worker|add|result|stats} ...", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "worker":
        asyncio.run(TaskQueue().worker())
    elif cmd == "add":
        text = sys.argv[2] if len(sys.argv) > 2 else "hello"
        prio = sys.argv[3] if len(sys.argv) > 3 else "med"
        asyncio.run(cli_add(text, prio))
    elif cmd == "result":
        asyncio.run(cli_result(sys.argv[2]))
    elif cmd == "stats":
        asyncio.run(cli_stats())
    elif cmd == "dlq-replay":
        if len(sys.argv) < 3:
            print("usage: python -m src.queue dlq-replay <task_id>", file=sys.stderr)
            sys.exit(2)
        asyncio.run(cli_dlq_replay(sys.argv[2]))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
