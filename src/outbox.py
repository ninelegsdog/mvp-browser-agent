"""Транзакционный outbox: идемпотентная доставка событий.

Паттерн botkit-framework/core/outbox.py:
- emit() вставляет строку в таблицу outbox в той же транзакции, что и изменение бизнес-состояния.
- pending() возвращает необработанные строки (processed_at IS NULL) ORDER BY id.
- mark_processed() ставит processed_at после успешной доставки.
- Привязка к event.id для дедупликации при повторной доставке.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg

from src.config import settings
from src.security import assert_no_secrets_in_text

logger = logging.getLogger(__name__)

OUTBOX_TABLE = "outbox"


@dataclass(frozen=True)
class OutboxEvent:
    id: int
    event_type: str
    payload: Dict[str, Any]
    created_at: str
    processed_at: Optional[str] = None


class Outbox:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def ensure_schema(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {OUTBOX_TABLE} (
                    id SERIAL PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    processed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_outbox_unprocessed
                    ON {OUTBOX_TABLE} (processed_at, id)
                    WHERE processed_at IS NULL;
                """
            )

    async def emit(self, event_type: str, payload: Dict[str, Any], conn=None) -> int:
        """Вставить событие. Лучше в той же транзакции, что и бизнес-изменение."""
        payload_json = json.dumps(payload, ensure_ascii=False)
        assert_no_secrets_in_text(payload_json, "outbox.emit")
        created_at = datetime.now(timezone.utc).isoformat()

        if conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {OUTBOX_TABLE} (event_type, payload, created_at)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                event_type,
                payload_json,
                created_at,
            )
        else:
            async with self.pool.acquire() as c:
                row = await c.fetchrow(
                    f"""
                    INSERT INTO {OUTBOX_TABLE} (event_type, payload, created_at)
                    VALUES ($1, $2, $3)
                    RETURNING id
                    """,
                    event_type,
                    payload_json,
                    created_at,
                )
        return row["id"]

    async def pending(self, limit: int = 100) -> List[OutboxEvent]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, event_type, payload, created_at, processed_at
                FROM {OUTBOX_TABLE}
                WHERE processed_at IS NULL
                ORDER BY id
                LIMIT $1
                """,
                limit,
            )
        return [
            OutboxEvent(
                id=r["id"],
                event_type=r["event_type"],
                payload=json.loads(r["payload"]),
                created_at=r["created_at"],
                processed_at=r["processed_at"],
            )
            for r in rows
        ]

    async def mark_processed(self, event_id: int, processed_at: str = None) -> None:
        if processed_at is None:
            processed_at = datetime.now(timezone.utc).isoformat()
        async with self.pool.acquire() as conn:
            await conn.execute(
                f"UPDATE {OUTBOX_TABLE} SET processed_at = $1 WHERE id = $2",
                processed_at,
                event_id,
            )


class OutboxDispatcher:
    """Опросы outbox, публикует события, помечает обработанными.

    При сбое публикации — прерывает цикл (сохраняет порядок), не пропускает вперёд.
    """

    def __init__(self, outbox: Outbox, publisher=None, batch_size: int = 100):
        self.outbox = outbox
        self.publisher = publisher
        self.batch_size = batch_size

    async def run_once(self) -> int:
        events = await self.outbox.pending(limit=self.batch_size)
        delivered = 0
        for event in events:
            try:
                if self.publisher:
                    await self.publisher.publish(event)
                await self.outbox.mark_processed(event.id)
                delivered += 1
            except Exception as e:
                logger.warning("Outbox publish failed for event %d: %s", event.id, e)
                break  # сохраняем порядок
        return delivered

    async def run_forever(self, interval_seconds: float = 1.0) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(interval_seconds)


class LoggingPublisher:
    """Заглушка издателя без Redis: только логирует событие."""

    async def publish(self, event: OutboxEvent) -> bool:
        logger.info("Outbox event (log only): %s %s", event.event_type, event.id)
        return True
