# Развёртывание (DEPLOYMENT)

Инструкция для развёртывания MVP на VPS заказчика. Общее время развёртывания: **30–60 минут**.

## Предварительные требования

- VPS с Ubuntu 22.04 LTS или выше
- Docker + Docker Compose
- Домен или фиксированный IP
- Доступ к аккаунтам Ozon / Avito / WB (ввод с телефона заказчика)

## Шаг 1. Клонирование репозитория

```bash
git clone https://github.com/ninelegsdog/mvp-browser-agent.git
cd mvp-browser-agent
git checkout v0.1.0
```

## Шаг 2. Настройка переменных окружения

```bash
cp .env.example .env
nano .env
```

Обязательные переменные:

| Переменная | Описание | Пример |
|------------|----------|--------|
| `BOT_TOKEN` | Токен Telegram-бота (от @BotFather) | `123456789:ABCdefGHIjklMNOpqrsTUVwxyz` |
| `BOT_ADMIN_ID` | Ваш Telegram ID (только вы управляете ботом) | `123456789` |
| `DB_PASSWORD` | Пароль PostgreSQL | Сгенерируйте случайный |
| `REDIS_PASSWORD` | Пароль Redis | Сгенерируйте случайный |

## Шаг 3. Запуск инфраструктуры

```bash
# Создать сеть и volumes
docker compose -f docker-compose.prod.yml up -d

# Проверить состояние
docker compose -f docker-compose.prod.yml ps
```

## Шаг 4. Проверка здоровья

```bash
curl -s http://<ВАШ_IP>:9090/health | python3 -m json.tool
```

Ожидаемый ответ:
```json
{
  "status": "ok",
  "checks": {
    "redis": true,
    "postgres": true
  }
}
```

## Шаг 5. Инициализация базы данных

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U ${DB_USER} -d ${DB_NAME} -f /migrations/001_outbox.sql
```

## Шаг 6. Первый вход с телефона

1. Откройте в Telegram бота (токен из `BOT_TOKEN`)
2. Отправьте любую задачу (например, «закажи билеты»)
3. Следуйте инструкциям бота для входа по QR в браузер на VPS
4. Вход нужен **один раз** — сессия сохраняется в профиле Chromium

## Мониторинг после развёртывания

| Ресурс | URL | Назначение |
|--------|-----|-----------|
| Health check | `http://<IP>:9090/health` | Статус сервисов |
| Метрики | `http://<IP>:9090/metrics` | Prometheus scrape |
| Логи | `docker compose -f docker-compose.prod.yml logs -f` | Отладка |

## Остановка и обновление

```bash
# Остановка
docker compose -f docker-compose.prod.yml down

# Обновление до новой версии
git pull
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d
```

## Резервное копирование

Ежедневно (через cron или вручную):

```bash
# Redis
docker compose -f docker-compose.prod.yml exec redis redis-cli -a ${REDIS_PASSWORD} BGSAVE
docker cp <container_id>:/data/dump.rdb ./backups/redis-$(date +%F).rdb

# PostgreSQL
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U ${DB_USER} ${DB_NAME} > ./backups/db-$(date +%F).sql

# Evidence
tar czf ./backups/evidence-$(date +%F).tar.gz ./evidence/
```

## Устранение неполадок

См. `RUNBOOK.md` — раздел «Инциденты».
