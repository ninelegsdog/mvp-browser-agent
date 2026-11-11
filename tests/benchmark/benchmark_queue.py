"""Benchmark-тесты для критических путей."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fakeredis.aioredis import FakeRedis

from src.outbox import Outbox, OutboxEvent
from src.queue import Task, TaskQueue


async def benchmark_queue_enqueue_pop(queue: TaskQueue, n: int = 1000):
    """Бенчмарк: enqueue + pop n задач."""
    tasks = [Task(task_id=f"bench-{i}", input=f"benchmark task {i}", priority="med") for i in range(n)]

    # Измеряем enqueue
    start = time.perf_counter()
    for task in tasks:
        await queue.enqueue(task)
    enqueue_time = time.perf_counter() - start

    # Измеряем pop
    start = time.perf_counter()
    popped = []
    for _ in range(n):
        task = await queue.pop()
        if task is not None:
            popped.append(task)
    pop_time = time.perf_counter() - start

    return {
        "enqueue_tasks": n,
        "enqueue_time_sec": enqueue_time,
        "enqueue_ops_per_sec": n / enqueue_time if enqueue_time > 0 else 0,
        "pop_tasks": len(popped),
        "pop_time_sec": pop_time,
        "pop_ops_per_sec": len(popped) / pop_time if pop_time > 0 else 0,
    }


async def benchmark_outbox_emit(outbox: Outbox, n: int = 1000):
    """Бенчмарк: outbox.emit n событий."""
    events = [
        OutboxEvent(
            id=i,
            event_type=f"event.{i % 5}",
            payload={"data": f"payload-{i}"},
            created_at="2026-11-11T00:00:00Z",
        )
        for i in range(n)
    ]

    start = time.perf_counter()
    for event in events:
        await outbox.emit(event.event_type, event.payload)
    emit_time = time.perf_counter() - start

    return {
        "emit_events": n,
        "emit_time_sec": emit_time,
        "emit_ops_per_sec": n / emit_time if emit_time > 0 else 0,
    }


async def benchmark_outbox_pending(outbox: Outbox, n: int = 1000):
    """Бенчмарк: outbox.pending n событий."""
    # Сначала создаем события
    for i in range(n):
        await outbox.emit(f"event.{i}", {"data": f"payload-{i}"})

    start = time.perf_counter()
    events = await outbox.pending(limit=n)
    pending_time = time.perf_counter() - start

    return {
        "pending_events": len(events),
        "pending_time_sec": pending_time,
        "pending_ops_per_sec": len(events) / pending_time if pending_time > 0 else 0,
    }


async def run_benchmarks():
    """Запуск всех бенчмарков."""
    print("=" * 60)
    print("BENCHMARK Результаты")
    print("=" * 60)

    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    # Очередь
    print("\n--- Очередь задач ---")
    queue_result = await benchmark_queue_enqueue_pop(q, n=1000)
    print(f"Enqueue: {queue_result['enqueue_ops_per_sec']:.0f} оп/сек")
    print(f"Pop: {queue_result['pop_ops_per_sec']:.0f} оп/сек")

    # Outbox
    print("\n--- Outbox ---")
    # Используем тот же Redis для простоты
    outbox = Outbox.__new__(Outbox)
    outbox._pool = r

    emit_result = await benchmark_outbox_emit(outbox, n=1000)
    print(f"Emit: {emit_result['emit_ops_per_sec']:.0f} оп/сек")

    pending_result = await benchmark_outbox_pending(outbox, n=1000)
    print(f"Pending: {pending_result['pending_ops_per_sec']:.0f} оп/сек")

    await r.aclose()

    print("\n" + "=" * 60)
    print("Бенчмарк завершен")


if __name__ == "__main__":
    asyncio.run(run_benchmarks())
