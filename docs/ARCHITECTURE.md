# Архитектура MVP: Персональный помощник-покупатель

Этот документ описывает систему с точки зрения архитектуры, чтобы заказчик мог оценить надёжность и масштабируемость перед подписанием договора.

## Общая схема

```mermaid
graph TD
    Client[Заказчик в Telegram] -->|Текст / Голос| Bot[aiogram Бот]
    Bot -->|Создание задачи| Queue[(Redis Очередь)]
    Queue -->|high / med / low| Worker[Воркер задач]
    Worker -->|Выбор инструментов| Agent[Агент-цикл]
    
    Agent -->|shell / HTTP / файлы / память| Tools[Инструменты]
    Tools -->|браузер по сценарию| Browser[Playwright + Chromium]
    
    Browser -->|вход по QR| Web[Ozon / Avito / WB / Почта]
    Browser -->|скриншот + видео| Evidence[(Evidence хранилище)]
    
    Worker -->|события| Outbox[(PostgreSQL Outbox)]
    Outbox -->|идемпотентная доставка| Dispatcher[Outbox Dispatcher]
    
    Worker -->|метрики| Metrics[Prometheus / Grafana]
    Metrics -->|алерты| Alerts[Telegram Алерты]
    
    Bot -->|чтение артефактов| Evidence
    
    style Client fill:#e1f5e1,stroke:#333
    style Bot fill:#fff3e0,stroke:#333
    style Queue fill:#e3f2fd,stroke:#333
    style Browser fill:#fce4ec,stroke:#333
    style Evidence fill:#f3e5f5,stroke:#333
```

## Компоненты

### 1. Очередь задач (`src/queue.py`)
- **Реализация:** Redis (`LPUSH` producer / `BLPOP` consumer)
- **Приоритеты:** `high` → `med` → `low` (строгая очередь через шардирование)
- **Надёжность:** Retry-or-DLQ (2 повтора, затем `queue:dead`)
- **Идемпотентность:** `idempotency_key` защищает от дублей
- **TTL:** Задачи старше 24 часов пропускаются
- **Грейсфул шатдаун:** Завершает in-flight задачи перед выходом

### 2. Агент-цикл (`src/agent.py`)
- **Модель выбирает инструменты**, не действия в браузере
- **Инструменты:** shell, HTTP, файлы, память, браузер (по сценарию)
- **Состояние задачи:** создано → план → шаг → ждёт вас → готово
- **FSM:** Подтверждение пользователя перед необратимыми действиями

### 3. Браузерная автоматизация (`src/browser.py`)
- **Реализация:** Playwright + постоянный профиль Chromium
- **Вход:** QR-код (один раз с телефона, сессия сохраняется)
- **Безопасность:** Профиль `0700`, вне git, отдельные профили по аккаунтам
- **Доказательство:** Скриншот + видео + метаданные на каждом шаге

### 4. Доказательство результата (`src/proof.py`)
- **Схема:** `defect.v1` при сбое, `result.v1.json` при успехе
- **Read-only:** Агент читает артефакты, не может их модифицировать
- **Хранение:** `evidence/<task_id>/` — версияция прогона

### 5. Транзакционный outbox (`src/outbox.py`)
- **Реализация:** PostgreSQL таблица `outbox`
- **Гарантия:** Событие пишется в ту же транзакцию, что и бизнес-изменение
- **Доставка:** `OutboxDispatcher` — при сбое публикации прерывает цикл (сохраняет порядок)
- **Идемпотентность:** Redis Stream ID `f"{event.id}-0"` для дедупликации

## Модель безопасности

```mermaid
graph LR
    Client[Клиент] -->|QR вход| Browser[Браузер VPS]
    Browser -->|Сессия 0700| Profile[(Профиль Chromium)]
    Profile -->|Куки / Сессии| Sites[Ozon / Avito / WB]
    
    Bot[Telegram Бот] -->|Только админ| Agent[Агент]
    Agent -->|Подтверждение| Client
    
    Secrets[Секреты] -->|.env.age| Env[Переменные окружения]
    Secrets -->|~/.secrets/keys/| Keys[Ключи 0600]
    
    Logs[Логи] -->|assert_no_secrets_in_text| Clean[Очищенные логи]
    
    style Profile fill:#ffe0e0,stroke:#c00
    style Secrets fill:#e0ffe0,stroke:#0c0
```

## Стек технологий

| Слой | Технология | Версия |
|------|-----------|--------|
| Язык | Python | 3.12 |
| Очередь | Redis | 7 |
| БД | PostgreSQL | 15 |
| Браузер | Playwright + Chromium | 1.49 |
| Бот | aiogram | 3.4 |
| Конфигурация | pydantic-settings | 2.16 |
| Мониторинг | Prometheus client | 0.26 |
| Веб | aiohttp | 3.11 |
| Контейнеризация | Docker | multi-stage |
