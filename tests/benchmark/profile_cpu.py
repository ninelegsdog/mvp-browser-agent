"""Профилирование CPU для критических путей MVP."""

import cProfile
import io
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fakeredis.aioredis import FakeRedis

from src.queue import Task, TaskQueue


def profile_queue_enqueue_pop(n: int = 5000):
    """Профилирование enqueue/pop n задач."""
    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    tasks = [Task(task_id=f"bench-{i}", input=f"benchmark task {i}", priority="med") for i in range(n)]

    # Профилируем enqueue
    profiler = cProfile.Profile()
    profiler.enable()
    for task in tasks:
        asyncio.run(q.enqueue(task))
    profiler.disable()

    stats = io.StringIO()
    ps = pstats.Stats(profiler, stream=stats).sort_stats("cumulative")
    ps.print_stats(20)
    print(stats.getvalue())

    # Профилируем pop
    profiler2 = cProfile.Profile()
    profiler2.enable()
    for _ in range(n):
        asyncio.run(q.pop())
    profiler2.disable()

    stats2 = io.StringIO()
    ps2 = pstats.Stats(profiler2, stream=stats2).sort_stats("cumulative")
    ps2.print_stats(20)
    print(stats2.getvalue())

    asyncio.run(r.aclose())


def profile_outbox_emit(n: int = 5000):
    """Профилирование outbox.emit n событий."""
    import asyncio

    from src.outbox import Outbox, OutboxEvent

    class FakePool:
        async def acquire(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, *args, **kwargs):
            pass

        async def fetchrow(self, *args, **kwargs):
            class Row:
                def __getitem__(self, key):
                    return 1

            return Row()

        async def fetch(self, *args, **kwargs):
            return []

    outbox = Outbox(FakePool())

    tasks = [
        OutboxEvent(
            id=i,
            event_type=f"event.{i % 5}",
            payload={"data": f"payload-{i}"},
            created_at="2026-11-11T00:00:00Z",
        )
        for i in range(n)
    ]

    profiler = cProfile.Profile()
    profiler.enable()
    for event in tasks:
        asyncio.run(outbox.emit(event.event_type, event.payload))
    profiler.disable()

    stats = io.StringIO()
    ps = pstats.Stats(profiler, stream=stats).sort_stats("cumulative")
    ps.print_stats(20)
    print(stats.getvalue())


if __name__ == "__main__":
    import asyncio

    print("=" * 60)
    print("Профилирование очереди задач")
    print("=" * 60)
    profile_queue_enqueue_pop(n=5000)

    print("\n" + "=" * 60)
    print("Профилирование outbox emit")
    print("=" * 60)
    profile_outbox_emit(n=5000)
