# MVP: Персональный помощник-покупатель (агент в браузере на VPS)

Демо-MVP, показывающий стек и подходы для реализации персонального помощника покупателя:
ручной вход с телефона в браузер на VPS заказчика, агент-очередь задач, доказательство результата,
транзакционный outbox, сопровождение и мониторинг.

## Что démontriert этот MVP

| Компонент | Паттерн | Доказательство в коде |
|-----------|---------|----------------------|
| Очередь задач | Redis + воркеры, DLQ, приоритеты, повтор после сбоя | `src/queue.py` |
| Браузер с сессией | Playwright + постоянный профиль, вход по QR, профиль 0700 вне git | `src/browser.py` |
| Доказательство результата | Trace, скриншот, видео на сбое, read-only доступ к артефактам | `src/proof.py` |
| Outbox / идемпотентность | Транзакционная таблица, repeat не создаёт дубль | `src/outbox.py` |
| Агент-цикл | Модель выбирает инструменты (shell/HTTP/файлы/память), не действия в браузере | `src/agent.py` |
| Telegram-интерфейс | FSM-состояния задачи, кнопки стоп / отменить / продолжить, голос → текст | `src/telegram_bot.py` |
| Безопасность | `.env` → `.env.age`, секреты вне кода, профили 0700 | `.env.example`, `src/security.py` |
| Мониторинг | Prometheus RED метрики, алерты, health check | `src/metrics.py` |

## Быстрый старт

### 1. Переменные окружения

```bash
cd /home/algtro/mvp-browser-agent
cp .env.example .env
```

Отредактируйте `.env` — укажите `BOT_TOKEN`, `BOT_ADMIN_ID`, `DB_PASSWORD`, `REDIS_PASSWORD`.

### 2. Зависимости

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
```

### 3. Инфраструктура (Redis + PostgreSQL)

```bash
docker compose up -d
```

Или запустите отдельно:

```bash
# PostgreSQL (нужен для outbox)
docker run --name mvp-postgres -e POSTGRES_DB=mvp_agent -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d postgres:15-alpine

# Применение миграции outbox
psql -h localhost -U postgres -d mvp_agent -f migrations/001_outbox.sql
```

### 4. Запуск воркера очереди

```bash
python -m src.queue worker
```

### 5. Запуск Telegram-бота

```bash
python -m src.telegram_bot
```

### 6. Тесты

```bash
pytest
```

### 7. Демо

```bash
python demo.py
```

## Архитектура

```
Заказчик (Telegram)
    │
    ▼
aiogram бот (FSM: создано → план → шаг → ждёт вас → готово)
    │
    ▼
Redis очередь (high / med / low) + DLQ (queue:dead)
    │
    ▼
Воркер задач ──► Агент выбирает инструменты (shell / HTTP / файлы / память)
    │                              │
    │                              ▼
    │                          Playwright (постоянный профиль Chromium)
    │                              │
    │                              ▼
    │                        Официальные сайты (Ozon / Авито / WB / билеты / почта)
    │
    ▼
Доказательство: trace + скриншот + видео на сбое (схема defect.v1)
    │
    ▼
Outbox (PostgreSQL) → события → идемпотентная доставка
    │
    ▼
Метрики Prometheus + алерты в Telegram
```

## Директории

| Путь | Назначение |
|------|-----------|
| `src/` | Исходный код MVP |
| `src/pages/` | Page Object Model (Ozon и др.) |
| `tests/` | Юнит-тесты (могут падать при мутации) |
| `migrations/` | SQL-миграции БД (outbox) |
| `evidence/` | Артефакты прогонов (trace, скриншоты, видео) — не в git |
| `.github/workflows/` | CI pipeline |

## Ограничения (честно, в коде и документации)

- Модель **не выбирает** действия в браузере — только инструменты (shell/HTTP/файлы/память).
- Браузерная часть работает по заданному сценарию, а не автономно.
- Ozon / Авито / WB — нет публичного покупательского API, работа через браузер с учётом лимитов и антибота.
- Необратимые действия (оплата, оформление заказа) — только с подтверждением заказчика в Telegram.
- Сканирование ≠ визит/покупка (если применимо к билетам).
- ИИ остаётся в контуре чтения: никаких автономных списаний, скидок, рекламных рассылок в первом релизе.
- НПД: работа по прямому договору (не через платформу, иначе лимит 60 ч/мес с одним заказчиком).

## Развёртывание на VPS

```bash
# Собрать образ
docker build -t mvp-browser-agent .

# Запустить через docker compose
docker compose -f docker-compose.prod.yml up -d

# Проверить здоровье
curl http://localhost:9090/health
```

## Тестирование

```bash
# Все тесты
pytest

# С покрытием
pytest --cov=src --cov-report=term-missing
```

## Лицензия

Демо для демонстрации подходов. Используйте как шаблон, не как производство.

## Этапы развития (по плану)

1. **Фаза 1 (готово):** Фундамент — `__init__.py`, `.gitignore`, pinned зависимости, ruff, BOT_ADMIN_ID whitelist, защита секретов в логах.
2. **Фаза 2 (готово):** Очередь и агент — подключение агента к очереди, idempotency_key, TTL задач, тесты.
3. **Фаза 3 (готово):** Браузер и доказательство — video record, health check сайтов, блокировка ресурсов, Page Object Model.
4. **Фаза 4 (готово):** Outbox и надёжность — категоризация ошибок, replay DLQ, graceful shutdown, интеграционные тесты.
5. **Фаза 5 (готово):** Развёртывание и CI/CD — Dockerfile, docker-compose.prod.yml, health check, pre-commit, GitHub Actions.
6. **Фаза 6 (готово):** Документация и приёмка — обновлённый README, RUNBOOK.
