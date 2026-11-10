# Changelog

Все значимые изменения в этом проекте документированы в этом файле.

Формат следует [Keep a Changelog](https://keepachangelog.com/ru/1.0.0/), а версия проекта соответствует [Semantic Versioning](https://semver.org/lang/ru/).

## [0.1.0] — 2026-11-10

### Добавлено
- Очередь задач на Redis с приоритетами high/med/low, DLQ, retry-or-DLQ
- Агент-цикл с выбором инструментов (shell/HTTP/файлы/память/браузер)
- Браузер с постоянным профилем Chromium, вход по QR, профиль 0700 вне git
- Доказательство результата: скриншот + видео + схема defect.v1, read-only доступ
- Транзакционный outbox в PostgreSQL с идемпотентной доставкой
- Telegram-бот на aiogram 3 с FSM состояниями, кнопками стоп/отменить/продолжить
- Белый список BOT_ADMIN_ID для управления ботом
- Prometheus RED метрики, эндпоинты /health и /metrics
- Health check с проверкой Redis и PostgreSQL
- Docker multi-stage build, пользователь UID 1001, docker-compose.prod.yml
- GitHub Actions CI (lint → test → build), pre-commit хуки
- Документация: ARCHITECTURE, DEPLOYMENT, API, SECURITY, ROADMAP, RUNBOOK
- Тесты: 30 юнит-тестов, покрытие 45%

### Безопасность
- assert_no_secrets_in_text — защита от утечек секретов в логах
- .env в .gitignore, пример в .env.example
- Профили браузера в .gitignore, права 0700

### Исправлено
- Исправлена ошибка импорта aioredis на redis.asyncio (совместимость с Python 3.12)
- Исправлены корейские символы в документации
- Исправлены опечатки в русскоязычных строках

## [0.0.1] — 2026-11-09

### Добавлено
- Начальная версия MVP
- Заглушка очереди задач
- Базовая структура проекта
