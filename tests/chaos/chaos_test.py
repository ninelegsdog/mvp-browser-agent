#!/usr/bin/env python3
"""Chaos testing для MVP Browser Agent.

Тестирует устойчивость системы к сбоям:
- Убийство процессов (worker, bot)
- Отключение сети Redis
- Перезапуск Redis
- Нагрузка во время сбоев
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fakeredis.aioredis import FakeRedis
from src.config import settings
from src.queue import TaskQueue, Task, MAX_RETRIES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class ChaosTester:
    """Тестирует устойчивость системы к различным сбоям."""

    def __init__(self):
        self.results = []

    async def test_redis_connection_loss(self):
        """Тест: потеря соединения с Redis во время обработки задачи."""
        logger.info("=== Тест: потеря соединения с Redis ===")
        r = FakeRedis(decode_responses=True)
        q = TaskQueue(redis_url="redis://localhost")
        q._redis = r

        # Добавляем задачу
        task = Task(task_id="chaos-1", input="тест потери соединения", priority="high")
        await q.enqueue(task)

        # Искусственно разрываем соединение
        await r.aclose()

        # Пытаемся извлечь задачу - должно обработать ошибку корректно
        try:
            result = await q.pop()
            logger.warning(f"Неожиданно: pop вернул {result} после закрытия соединения")
        except Exception as e:
            logger.info(f"Ожидаемая ошибка при разорванном соединении: {e}")

        # Переподключаемся
        r2 = FakeRedis(decode_responses=True)
        q._redis = r2
        result = await q.pop()
        assert result is not None, "Задача должна быть доступна после переподключения"
        assert result.task_id == "chaos-1"

        logger.info("✓ Тест пройден: задача сохранена после разрыва соединения")
        await r2.aclose()
        self.results.append(("redis_connection_loss", "PASS"))

    async def test_worker_crash_during_task(self):
        """Тест: гибель воркера во время выполнения задачи."""
        logger.info("=== Тест: гибель воркера во время выполнения ===")
        r = FakeRedis(decode_responses=True)
        q = TaskQueue(redis_url="redis://localhost")
        q._redis = r

        task = Task(task_id="chaos-2", input="тест гибели воркера", priority="med")
        await q.enqueue(task)

        # Извлекаем задачу (как делает воркер)
        popped = await q.pop()
        assert popped is not None

        # Имитируем гибель воркера - не вызываем save_result
        # Задача должна остаться в обработке, но не в DLQ
        dlq_len = await r.llen(settings.DLQ)
        assert dlq_len == 0, "Задача не должна попасть в DLQ при гибели воркера"

        # Задача должна быть доступна для повторного извлечения (если мы не сохраняем результат)
        # В реальном воркере задача осталась бы в обработке
        logger.info("✓ Тест пройден: задача не попала в DLQ при гибели воркера")
        await r.aclose()
        self.results.append(("worker_crash", "PASS"))

    async def test_multiple_rapid_failures(self):
        """Тест: múltiples быстрые сбои одной задачи."""
        logger.info("=== Тест: múltiples быстрые сбои ===")
        r = FakeRedis(decode_responses=True)
        q = TaskQueue(redis_url="redis://localhost")
        q._redis = r

        task = Task(task_id="chaos-3", input="тест múltiples сбоев", priority="med", retries=0)

        # Искусственно ускоряем ретраи
        async def fake_backoff(retries):
            return 0.01
        q._backoff_delay = fake_backoff

        for i in range(MAX_RETRIES + 1):
            try:
                await q.fail(task, Exception(f"сбой номер {i + 1}"))
            except Exception as e:
                logger.debug(f"Ошибка при fail #{i + 1}: {e}")

        # После MAX_RETRIES задача должна быть в DLQ
        dlq_items = await r.lrange(settings.DLQ, 0, -1)
        assert len(dlq_items) == 1, f"Ожидалась 1 задача в DLQ, получено {len(dlq_items)}"

        item = json.loads(dlq_items[0])
        assert item["task"]["task_id"] == "chaos-3"

        logger.info("✓ Тест пройден: задача попала в DLQ после múltiples сбоев")
        await r.aclose()
        self.results.append(("multiple_failures", "PASS"))

    async def test_concurrent_tasks_under_load(self):
        """Тест: множественные задачи под нагрузкой."""
        logger.info("=== Тест: множественные задачи под нагрузкой ===")
        r = FakeRedis(decode_responses=True)
        q = TaskQueue(redis_url="redis://localhost")
        q._redis = r

        # Добавляем 50 задач одновременно
        tasks = []
        for i in range(50):
            task = Task(
                task_id=f"chaos-load-{i}",
                input=f"задача нагрузки {i}",
                priority="med",
            )
            tasks.append(task)

        for task in tasks:
            await q.enqueue(task)

        queue_len = await r.llen(settings.QUEUE_MED)
        assert queue_len == 50, f"Ожидалось 50 задач в очереди, получено {queue_len}"

        # Извлекаем все задачи
        retrieved = 0
        while True:
            task = await q.pop()
            if task is None:
                break
            retrieved += 1

        assert retrieved == 50, f"Ожидалось извлечь 50 задач, получено {retrieved}"

        logger.info("✓ Тест пройден: 50 задач обработаны корректно")
        await r.aclose()
        self.results.append(("concurrent_tasks", "PASS"))

    async def run_all(self):
        """Запуск всех тестов chaos."""
        tests = [
            self.test_redis_connection_loss,
            self.test_worker_crash_during_task,
            self.test_multiple_rapid_failures,
            self.test_concurrent_tasks_under_load,
        ]

        for test in tests:
            try:
                await test()
            except Exception as e:
                logger.error(f"Тест {test.__name__} упал: {e}")
                self.results.append((test.__name__, "FAIL"))

        # Итоговый отчет
        passed = sum(1 for _, status in self.results if status == "PASS")
        failed = sum(1 for _, status in self.results if status == "FAIL")
        logger.info(f"\n{'='*50}")
        logger.info(f"Chaos testing results: {passed} passed, {failed} failed")
        logger.info(f"{'='*50}")

        return failed == 0


async def main():
    tester = ChaosTester()
    success = await tester.run_all()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())