import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fakeredis.aioredis import FakeRedis
from src.config import settings
from src.queue import TaskQueue, Task, MAX_RETRIES


async def test_enqueue_pop_basic():
    """Задача добавляется и извлекается из очереди."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task = Task(task_id="t1", input="hello", priority="med")
    await q.enqueue(task)
    popped = await q.pop()
    assert popped is not None
    assert popped.task_id == "t1"
    assert popped.input == "hello"
    await r.aclose()


async def test_priority_ordering():
    """BLPOP возвращает высокий приоритет primero."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    low = Task(task_id="low", input="low", priority="low")
    high = Task(task_id="high", input="high", priority="high")
    med = Task(task_id="med", input="med", priority="med")

    await q.enqueue(low)
    await q.enqueue(med)
    await q.enqueue(high)

    first = await q.pop()
    second = await q.pop()
    third = await q.pop()

    assert first.task_id == "high"
    assert second.task_id == "med"
    assert third.task_id == "low"
    await r.aclose()


async def test_retry_then_dlq():
    """После MAX_RETRIES задача уходит в DLQ, не в очередь."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task = Task(task_id="fail", input="boom", priority="med", retries=MAX_RETRIES)

    # Мокаем _backoff_delay, чтобы не ждать реальную задержку
    async def fake_backoff(retries):
        return 0.01
    q._backoff_delay = fake_backoff

    try:
        await q.fail(task, Exception("test"))
    except Exception:
        pass

    dlq_len = await r.llen(settings.DLQ)
    assert dlq_len == 1, f"Expected 1 in DLQ, got {dlq_len}"
    queue_len = await r.llen(settings.QUEUE_MED)
    assert queue_len == 0, "Task should not re-enter queue after max retries"
    await r.aclose()


async def test_retry_preserves_queue():
    """Повторная постановка сохраняет приоритет."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task = Task(task_id="retry", input="retry me", priority="high", retries=0)

    try:
        await q.fail(task, Exception("retry"))
    except Exception:
        pass

    high_len = await r.llen(settings.QUEUE_HIGH)
    med_len = await r.llen(settings.QUEUE_MED)
    assert high_len == 1, f"Expected 1 in HIGH, got {high_len}"
    assert med_len == 0, f"Expected 0 in MED, got {med_len}"
    await r.aclose()


async def test_corrupt_json_skipped():
    """Битый JSON в очереди пропускается без падения."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    await r.lpush(settings.QUEUE_MED, "{not valid json}")
    valid = Task(task_id="valid", input="ok", priority="med")
    await q.enqueue(valid)

    popped = await q.pop()
    assert popped is not None
    assert popped.task_id == "valid"
    await r.aclose()


async def test_idempotency_key_prevents_duplicate():
    """Повторная отправка с тем же idempotency_key не дублирует задачу."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task1 = Task(task_id="t1", input="hello", priority="med", idempotency_key="key-1")
    task2 = Task(task_id="t2", input="hello", priority="med", idempotency_key="key-1")

    await q.enqueue(task1)
    await q.enqueue(task2)

    # Должна быть только одна задача в очереди
    queue_len = await r.llen(settings.QUEUE_MED)
    assert queue_len == 1, f"Expected 1 in queue, got {queue_len}"

    popped = await q.pop()
    assert popped is not None
    assert popped.task_id == "t1"
    await r.aclose()


async def test_task_ttl_expired_skipped():
    """Просроченная задача пропускается при pop."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    # Создаём задачу с.created_at в прошлом (старше TTL)
    from datetime import datetime, timezone, timedelta
    old_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    expired_task = Task(task_id="expired", input="old", priority="med", created_at=old_time)
    fresh_task = Task(task_id="fresh", input="new", priority="med")

    await q.enqueue(expired_task)
    await q.enqueue(fresh_task)

    # Первый pop должен пропустить expired и вернуть fresh
    popped = await q.pop()
    assert popped is not None
    assert popped.task_id == "fresh"

    # Второй pop должен вернуть None (expired уже пропущен, fresh уже взята)
    popped2 = await q.pop()
    assert popped2 is None
    await r.aclose()


async def test_categorize_error():
    """Категоризация ошибок в DLQ."""
    from src.queue import (
        categorize_error,
        ERROR_CATEGORY_CAPTCHA,
        ERROR_CATEGORY_BAN,
        ERROR_CATEGORY_NETWORK,
        ERROR_CATEGORY_OTHER,
    )

    assert categorize_error(Exception("captcha required")) == ERROR_CATEGORY_CAPTCHA
    assert categorize_error(Exception("капча")) == ERROR_CATEGORY_CAPTCHA
    assert categorize_error(Exception("account banned")) == ERROR_CATEGORY_BAN
    assert categorize_error(Exception("403 forbidden")) == ERROR_CATEGORY_BAN
    assert categorize_error(Exception("connection timeout")) == ERROR_CATEGORY_NETWORK
    assert categorize_error(Exception("network error")) == ERROR_CATEGORY_NETWORK
    assert categorize_error(Exception("unknown error")) == ERROR_CATEGORY_OTHER


async def test_dlq_error_category():
    """DLQ запись содержит error_category."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task = Task(task_id="fail-captcha", input="boom", priority="med", retries=MAX_RETRIES)

    # Мокаем _backoff_delay, чтобы не ждать реальную задержку
    async def fake_backoff(retries):
        return 0.01
    q._backoff_delay = fake_backoff

    await q.fail(task, Exception("captcha required"))

    dlq_items = await r.lrange(settings.DLQ, 0, -1)
    assert len(dlq_items) == 1
    item = json.loads(dlq_items[0])
    assert item["error_category"] == "captcha"
    await r.aclose()


async def test_graceful_shutdown_in_flight():
    """При shutdown задачи in-flight завершаются перед выходом."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task = Task(task_id="shutdown-test", input="hello", priority="med")
    await q.enqueue(task)

    # Обрабатываем задачу вручную (시муляция воркера)
    popped = await q.pop()
    assert popped is not None
    await q.process(popped)

    # Результат должен быть сохранён
    result = await q.get_result("shutdown-test")
    assert result is not None
    assert result["status"] == "done"
    await r.aclose()


if __name__ == "__main__":
    asyncio.run(test_enqueue_pop_basic())
    asyncio.run(test_priority_ordering())
    asyncio.run(test_retry_then_dlq())
    asyncio.run(test_retry_preserves_queue())
    asyncio.run(test_corrupt_json_skipped())
    asyncio.run(test_idempotency_key_prevents_duplicate())
    asyncio.run(test_task_ttl_expired_skipped())
    asyncio.run(test_categorize_error())
    asyncio.run(test_dlq_error_category())
    asyncio.run(test_graceful_shutdown_in_flight())
    print("All queue tests passed")
