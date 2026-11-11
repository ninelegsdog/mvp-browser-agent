"""Pydantic модели для межсервисных сообщений.

Обеспечивают типизацию и валидацию данных между компонентами системы:
- Задачи очереди
- Результаты выполнения
- События outbox
- Доказательства (proof artifacts)
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, validator


class TaskStatus(StrEnum):
    CREATED = "created"
    PLAN = "plan"
    STEP = "step"
    AWAITS_USER = "awaits_user"
    DONE = "done"
    FAILED = "failed"


class TaskPriority(StrEnum):
    HIGH = "high"
    MED = "med"
    LOW = "low"


class TaskCreate(BaseModel):
    """Модель для создания новой задачи."""

    input: str = Field(..., min_length=1, max_length=10000, description="Текст задачи")
    priority: TaskPriority = Field(default=TaskPriority.MED, description="Приоритет задачи")
    type: str = Field(default="echo", description="Тип задачи")
    command: str | None = Field(default=None, description="Команда для shell-задач")
    path: str | None = Field(default=None, description="Путь для wiki-задач")
    idempotency_key: str | None = Field(default=None, description="Ключ идемпотентности")
    extra: dict[str, Any] = Field(default_factory=dict, description="Дополнительные данные")

    @validator("input")
    def input_not_empty(cls, v):  # noqa: N805 — pydantic-валидатор требует cls
        if not v or not v.strip():
            raise ValueError("input не может быть пустым")
        return v.strip()


class TaskResponse(BaseModel):
    """Модель ответа о задаче."""

    task_id: str
    input: str
    created_at: datetime
    retries: int
    priority: TaskPriority
    type: str
    command: str | None = None
    path: str | None = None
    idempotency_key: str | None = None
    status: TaskStatus
    current_step: int = 0
    result: dict[str, Any] | None = None
    awaits_user: bool = False

    class Config:
        from_attributes = True


class TaskResult(BaseModel):
    """Модель результата выполнения задачи."""

    task_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
    error_type: str | None = None
    error_category: str | None = None
    completed_at: datetime | None = None


class OutboxEventCreate(BaseModel):
    """Модель для создания события outbox."""

    event_type: str = Field(..., min_length=1, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class OutboxEventResponse(BaseModel):
    """Модель события outbox."""

    id: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
    processed_at: datetime | None = None

    class Config:
        from_attributes = True


class EvidenceArtifact(BaseModel):
    """Модель доказательства выполнения."""

    task_id: str
    step: str
    timestamp: datetime
    data: dict[str, Any] | None = None
    screenshot: str | None = None
    video: str | None = None

    class Config:
        from_attributes = True


class DefectV1(BaseModel):
    """Схема дефекта (defect.v1) для браузерных сбоев."""

    task_id: str
    status: str = "failed"
    error: str
    error_type: str
    timestamp: datetime
    steps: list[dict[str, Any]]
    extra: dict[str, Any] | None = None

    class Config:
        from_attributes = True


class HealthCheckResponse(BaseModel):
    """Ответ эндпоинта здоровья."""

    status: str
    checks: dict[str, bool]


class AlertResponse(BaseModel):
    """Ответ эндпоинта алертов."""

    alert: str | None = None
    current_rate: float | None = None
    threshold: float | None = None
    message: str | None = None
