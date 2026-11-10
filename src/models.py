"""Pydantic модели для межсервисных сообщений.

Обеспечивают типизацию и валидацию данных между компонентами системы:
- Задачи очереди
- Результаты выполнения
- События outbox
- Доказательства (proof artifacts)
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, validator


class TaskStatus(str, Enum):
    CREATED = "created"
    PLAN = "plan"
    STEP = "step"
    AWAITS_USER = "awaits_user"
    DONE = "done"
    FAILED = "failed"


class TaskPriority(str, Enum):
    HIGH = "high"
    MED = "med"
    LOW = "low"


class TaskCreate(BaseModel):
    """Модель для создания новой задачи."""
    input: str = Field(..., min_length=1, max_length=10000, description="Текст задачи")
    priority: TaskPriority = Field(default=TaskPriority.MED, description="Приоритет задачи")
    type: str = Field(default="echo", description="Тип задачи")
    command: Optional[str] = Field(default=None, description="Команда для shell-задач")
    path: Optional[str] = Field(default=None, description="Путь для wiki-задач")
    idempotency_key: Optional[str] = Field(default=None, description="Ключ идемпотентности")
    extra: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные данные")

    @validator("input")
    def input_not_empty(cls, v):
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
    command: Optional[str] = None
    path: Optional[str] = None
    idempotency_key: Optional[str] = None
    status: TaskStatus
    current_step: int = 0
    result: Optional[Dict[str, Any]] = None
    awaits_user: bool = False

    class Config:
        from_attributes = True


class TaskResult(BaseModel):
    """Модель результата выполнения задачи."""
    task_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    error_category: Optional[str] = None
    completed_at: Optional[datetime] = None


class OutboxEventCreate(BaseModel):
    """Модель для создания события outbox."""
    event_type: str = Field(..., min_length=1, max_length=100)
    payload: Dict[str, Any] = Field(default_factory=dict)


class OutboxEventResponse(BaseModel):
    """Модель события outbox."""
    id: int
    event_type: str
    payload: Dict[str, Any]
    created_at: datetime
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class EvidenceArtifact(BaseModel):
    """Модель доказательства выполнения."""
    task_id: str
    step: str
    timestamp: datetime
    data: Optional[Dict[str, Any]] = None
    screenshot: Optional[str] = None
    video: Optional[str] = None

    class Config:
        from_attributes = True


class DefectV1(BaseModel):
    """Схема дефекта (defect.v1) для браузерных сбоев."""
    task_id: str
    status: str = "failed"
    error: str
    error_type: str
    timestamp: datetime
    steps: List[Dict[str, Any]]
    extra: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


class HealthCheckResponse(BaseModel):
    """Ответ эндпоинта здоровья."""
    status: str
    checks: Dict[str, bool]


class AlertResponse(BaseModel):
    """Ответ эндпоинта алертов."""
    alert: Optional[str] = None
    current_rate: Optional[float] = None
    threshold: Optional[float] = None
    message: Optional[str] = None