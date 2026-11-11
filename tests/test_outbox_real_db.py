import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    import asyncpg
except ImportError:
    asyncpg = None

from src.outbox import LoggingPublisher, Outbox, OutboxDispatcher


async def test_outbox_real_db():
    """Интеграционный тест outbox с реальной PostgreSQL (пропускается если нет БД)."""
    if asyncpg is None:
        print("asyncpg не установлен, пропускаем")
        return

    try:
        conn = await asyncpg.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
            database=os.getenv("DB_NAME", "mvp_agent"),
        )
    except Exception:
        print("PostgreSQL недоступен, пропускаем")
        return

    try:
        # Применяем миграцию
        migration_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations", "001_outbox.sql")
        with open(migration_path) as f:
            migration_sql = f.read()
        await conn.execute(migration_sql)

        outbox = Outbox(conn)

        # emit
        event_id = await outbox.emit("order.paid", {"order_id": "42", "amount": 100})
        assert event_id == 1

        # pending
        events = await outbox.pending()
        assert len(events) == 1
        assert events[0].event_type == "order.paid"
        assert events[0].processed_at is None

        # mark_processed
        await outbox.mark_processed(event_id)
        events_after = await outbox.pending()
        assert len(events_after) == 0

        # dispatcher run_once with LoggingPublisher
        dispatcher = OutboxDispatcher(outbox, publisher=LoggingPublisher())
        await outbox.emit("test.event", {"data": "value"})
        delivered = await dispatcher.run_once()
        assert delivered == 1

        print("Outbox real DB test passed")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(test_outbox_real_db())
