# API и интеграция

MVP предоставляет программные интерфейсы для управления задачами и получения метрик.

## 1. HTTP-эндпоинты (Prometheus + Health)

| Метод | Путь | Описание | Ответ |
|-------|------|----------|-------|
| `GET` | `/health` | Проверка здоровья Redis и PostgreSQL | `{"status": "ok", "checks": {...}}` |
| `GET` | `/metrics` | Prometheus метрики (текстовый формат) | `text/plain` |

## 2. Очередь задач (CLI)

Запуск и управление через `python -m src.queue`:

| Команда | Аргументы | Описание |
|---------|-----------|----------|
| `worker` | — | Запуск воркера очереди |
| `add` | `<текст> [приоритет]` | Добавить задачу (high/med/low) |
| `result` | `<task_id>` | Получить результат выполнения |
| `stats` | — | Показать глубину очередей |
| `dlq-replay` | `<task_id>` | Вернуть задачу из DLQ в очередь |

Пример:
```bash
python -m src.queue add "закажи билеты в кино" high
# Task a1b2c3d4 enqueued (high)

python -m src.queue result a1b2c3d4
# {"task_id": "a1b2c3d4", "result": "done: ...", "type": "agent"}
```

## 3. Telegram-бот

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и инструкция |
| `/status` | Статус текущей задачи |
| `/evidence <id>` | Список артефактов доказательства (read-only) |

Бот отвечает только пользователю с `BOT_ADMIN_ID`.

## 4. Схема задачи (JSON)

```json
{
  "task_id": "uuid-v4",
  "input": "закажи билеты в кино",
  "created_at": "2026-11-10T12:00:00Z",
  "retries": 0,
  "priority": "med",
  "type": "agent",
  "idempotency_key": null,
  "extra": {}
}
```

## 5. Схема события Outbox

```json
{
  "id": 1,
  "event_type": "order.paid",
  "payload": {"order_id": "42", "amount": 100},
  "created_at": "2026-11-10T12:00:00Z",
  "processed_at": null
}
```

## 6. Схема доказательства (defect.v1)

```json
{
  "task_id": "uuid-v4",
  "status": "failed",
  "error": "captcha required",
  "error_type": "ValueError",
  "timestamp": "2026-11-10T12:05:00Z",
  "steps": [
    {
      "task_id": "uuid-v4",
      "step": "start",
      "timestamp": "2026-11-10T12:00:00Z",
      "data": {},
      "screenshot": "evidence/uuid-v4/step.png"
    }
  ],
  "extra": {}
}
```

## 7. Ограничения

- Ozon / Авито / WB: нет публичного покупательского API — работа через браузер
- Капча: проходит вручную с телефона заказчика один раз
- Необратимые действия: только с подтверждением в Telegram
- НПД: работа по прямому договору, не через платформу
