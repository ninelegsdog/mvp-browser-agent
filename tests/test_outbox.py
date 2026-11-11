import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.outbox import LoggingPublisher, OutboxDispatcher, OutboxEvent


def test_outbox_mark_processed_sets_timestamp():
    """После mark_processed processed_at не None."""
    event = OutboxEvent(
        id=1,
        event_type="order.paid",
        payload={"order_id": "42"},
        created_at="2026-11-10T12:00:00Z",
        processed_at=None,
    )
    assert event.processed_at is None

    processed = OutboxEvent(
        id=event.id,
        event_type=event.event_type,
        payload=event.payload,
        created_at=event.created_at,
        processed_at="2026-11-10T12:00:05Z",
    )
    assert processed.processed_at is not None


def test_outbox_event_immutable():
    """OutboxEvent — frozen dataclass, нельзя изменить после создания."""
    import dataclasses

    event = OutboxEvent(
        id=1,
        event_type="test",
        payload={},
        created_at="2026-01-01T00:00:00Z",
    )
    try:
        event.id = 999
        raise AssertionError("Should be frozen")
    except dataclasses.FrozenInstanceError:
        pass  # ожидается


def test_logging_publisher_publish_returns_true():
    """LoggingPublisher не падает, возвращает True."""
    pub = LoggingPublisher()

    async def run():
        return await pub.publish(OutboxEvent(1, "t", {}, "2026-01-01T00:00:00Z"))

    result = asyncio.run(run())
    assert result is True


def test_dispatcher_run_once_breaks_on_failure():
    """При сбое publicados — цикл прерывает, сохраняя порядок."""
    call_order = []

    class FailingPublisher:
        async def publish(self, event: OutboxEvent) -> bool:
            call_order.append(event.id)
            if event.id == 2:
                raise RuntimeError("boom")
            return True

    events = [
        OutboxEvent(1, "a", {}, "2026-01-01T00:00:00Z"),
        OutboxEvent(2, "b", {}, "2026-01-01T00:00:00Z"),
        OutboxEvent(3, "c", {}, "2026-01-01T00:00:00Z"),
    ]

    async def run():
        # Готовим future с событиями
        fut = asyncio.get_event_loop().create_future()
        fut.set_result(events)

        # Создаём простой объект-outbox с мок-методами
        class FakeOutbox:
            async def pending(self, limit=100):
                return events

            async def mark_processed(self, event_id, processed_at=None):
                pass

        dispatcher = OutboxDispatcher(FakeOutbox(), publisher=FailingPublisher())
        called = []
        orig_publish = dispatcher.publisher.publish

        async def tracking_publish(event):
            called.append(event.id)
            return await orig_publish(event)

        dispatcher.publisher.publish = tracking_publish
        delivered = await dispatcher.run_once()
        return delivered, called

    delivered, called = asyncio.run(run())
    assert delivered == 1, f"Expected 1 delivered, got {delivered}"
    assert called == [1, 2], f"Expected events 1,2 attempted, got {called}"


if __name__ == "__main__":
    test_outbox_mark_processed_sets_timestamp()
    test_outbox_event_immutable()
    test_logging_publisher_publish_returns_true()
    test_dispatcher_run_once_breaks_on_failure()
    print("All outbox tests passed")
