"""Интеграционные тесты с реальными сервисами через testcontainers.

Эти тесты требуют Docker и запускаются только если testcontainers установлен.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    from testcontainers.postgres import PostgresContainer
    from testcontainers.redis import RedisContainer

    HAS_TESTCONTAINERS = True
except ImportError:
    HAS_TESTCONTAINERS = False


if HAS_TESTCONTAINERS:
    import asyncpg

    from src.outbox import Outbox
    from src.queue import Task, TaskQueue

    async def test_queue_with_real_redis():
        """Интеграционный тест очереди с реальным Redis."""
        with RedisContainer("redis:7-alpine") as redis_container:
            redis_url = redis_container.get_connection_url()
            q = TaskQueue(redis_url=redis_url)
            await q.connect()

            task = Task(task_id="integration-1", input="integration test", priority="high")
            await q.enqueue(task)
            popped = await q.pop()
            assert popped is not None
            assert popped.task_id == "integration-1"
            await q.close()

    async def test_outbox_with_real_postgres():
        """Интеграционный тест outbox с реальным PostgreSQL."""
        with PostgresContainer("postgres:15-alpine") as postgres:
            conn_str = postgres.get_connection_url()
            # Парсим connection string для asyncpg
            # postgresql://user:password@host:port/db
            parts = conn_str.replace("postgresql://", "").split("/")
            db_name = parts[1]
            host_port = parts[0].split("@")[1]
            host = host_port.split(":")[0]
            port = host_port.split(":")[1]
            user_pass = parts[0].split("@")[0]
            user = user_pass.split(":")[0]
            password = user_pass.split(":")[1]

            conn = await asyncpg.connect(
                host=host,
                port=int(port),
                user=user,
                password=password,
                database=db_name,
            )

            try:
                # Применяем миграцию
                migration_path = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "migrations", "001_outbox.sql"
                )
                with open(migration_path) as f:
                    migration_sql = f.read()
                await conn.execute(migration_sql)

                outbox = Outbox(conn)
                event_id = await outbox.emit("integration.test", {"data": "value"})
                assert event_id == 1

                events = await outbox.pending()
                assert len(events) == 1
                await outbox.mark_processed(event_id)
                events_after = await outbox.pending()
                assert len(events_after) == 0

                print("Интеграционный тест outbox с PostgreSQL пройден")
            finally:
                await conn.close()

    async def main():
        await test_queue_with_real_redis()
        await test_outbox_with_real_postgres()
        print("Все интеграционные тесты пройдены")

    if __name__ == "__main__":
        asyncio.run(main())
else:
    print("testcontainers не установлен. Интеграционные тесты пропущены.")
    print("Установите: pip install testcontainers")
