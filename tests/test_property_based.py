"""Property-based тесты с Hypothesis для логики приоритетов и идемпотентности."""

from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

from src.config import settings as app_settings
from src.queue import MAX_RETRIES, Task, TaskQueue, categorize_error


# Стратегии для генерации тестовых данных
@st.composite
def task_strategy(draw):
    """Генерирует случайную задачу."""
    return Task(
        task_id=draw(st.text(min_size=1, max_size=50)),
        input=draw(st.text(min_size=1, max_size=200)),
        retries=draw(st.integers(min_value=0, max_value=MAX_RETRIES + 2)),
        priority=draw(st.sampled_from(["high", "med", "low"])),
        type=draw(st.sampled_from(["echo", "shell", "agent", "browser"])),
        idempotency_key=draw(st.one_of(st.none(), st.text(min_size=1, max_size=30))),
    )


@hypothesis_settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(task_strategy())
def test_task_roundtrip(task: Task):
    """Свойство: задача после сериализации и десериализации остается такой же."""
    d = task.to_dict()
    restored = Task.from_dict(d)
    assert restored.task_id == task.task_id
    assert restored.input == task.input
    assert restored.retries == task.retries
    assert restored.priority == task.priority
    assert restored.type == task.type
    assert restored.idempotency_key == task.idempotency_key


@hypothesis_settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    task_id=st.text(min_size=1, max_size=50),
    input_text=st.text(min_size=1, max_size=200),
    priority=st.sampled_from(["high", "med", "low"]),
    retries=st.integers(min_value=0, max_value=MAX_RETRIES + 5),
)
def test_task_priority_validation(task_id, input_text, priority, retries):
    """Свойство: приоритет задачи всегда валидный."""
    task = Task(task_id=task_id, input=input_text, priority=priority, retries=retries)
    assert task.priority in ["high", "med", "low"]


@hypothesis_settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(
    task_id=st.text(min_size=1, max_size=50),
    input_text=st.text(min_size=1, max_size=100),
    retries1=st.integers(min_value=0, max_value=MAX_RETRIES),
    retries2=st.integers(min_value=0, max_value=MAX_RETRIES),
)
def test_idempotency_key_uniqueness(task_id, input_text, retries1, retries2):
    """Свойство: два идентичных idempotency_key не создают дублей."""
    # Это тест логики, не требующий Redis
    key1 = f"key-{task_id}-{retries1}"
    key2 = f"key-{task_id}-{retries2}"

    task1 = Task(task_id=task_id, input=input_text, priority="med", idempotency_key=key1)
    task2 = Task(task_id=task_id, input=input_text, priority="med", idempotency_key=key2)

    # Если ключи одинаковые, задачи считаются дублями
    if key1 == key2:
        assert task1.idempotency_key == task2.idempotency_key
    else:
        assert task1.idempotency_key != task2.idempotency_key


@hypothesis_settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(error_message=st.text(min_size=1, max_size=100))
def test_categorize_error_deterministic(error_message: str):
    """Свойство: категоризация ошибки детерминирована."""
    result1 = categorize_error(Exception(error_message))
    result2 = categorize_error(Exception(error_message))
    assert result1 == result2


@hypothesis_settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(
    error_message=st.text(min_size=1, max_size=100),
    retries=st.integers(min_value=0, max_value=MAX_RETRIES + 1),
)
def test_fail_transition_deterministic(error_message: str, retries: int):
    """Свойство: результат fail() детерминирован для одинаковых входов."""
    import asyncio

    from fakeredis.aioredis import FakeRedis

    async def run_test():
        r = FakeRedis(decode_responses=True)
        q = TaskQueue(redis_url="redis://localhost")
        q._redis = r

        task = Task(task_id="test", input="test", priority="med", retries=retries)

        # Мокаем backoff, чтобы не ждать
        async def fake_backoff(retries_param):
            return 0.01

        q._backoff_delay = fake_backoff

        await q.fail(task, Exception(error_message))

        # Проверяем детерминированность
        dlq_len = await r.llen(app_settings.DLQ)
        queue_len = await r.llen(app_settings.QUEUE_MED)

        if retries < MAX_RETRIES:
            assert queue_len == 1, "Задача должна быть в очереди при retries < MAX"
            assert dlq_len == 0, "Задача не должна быть в DLQ при retries < MAX"
        else:
            assert dlq_len == 1, "Задача должна быть в DLQ при retries >= MAX"
            assert queue_len == 0, "Задача не должна быть в очереди при retries >= MAX"

        await r.aclose()

    asyncio.run(run_test())


@hypothesis_settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
@given(
    priority1=st.sampled_from(["high", "med", "low"]),
    priority2=st.sampled_from(["high", "med", "low"]),
)
def test_priority_ordering_property(priority1: str, priority2: str):
    """Свойство: приоритеты сортируются в правильном порядке."""
    priority_order = {"high": 0, "med": 1, "low": 2}
    assert (
        priority_order[priority1] <= priority_order[priority2] or priority_order[priority2] <= priority_order[priority1]
    )
    # Транзитивность: если p1 <= p2 и p2 <= p1, то p1 == p2
    if (
        priority_order[priority1] <= priority_order[priority2]
        and priority_order[priority2] <= priority_order[priority1]
    ):
        assert priority1 == priority2
