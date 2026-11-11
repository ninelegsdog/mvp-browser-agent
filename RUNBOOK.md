# Runbook: Персональный помощник-покупатель (MVP)

Эксплуатационная документация для запуска, мониторинга и устранения инцидентов.

## Быстрая справка

| Команда | Назначение |
|---------|-----------|
| `docker compose -f docker-compose.prod.yml up -d` | Запуск всех сервисов |
| `docker compose -f docker-compose.prod.yml logs -f` | Просмотр логов |
| `curl http://localhost:9090/health` | Проверка здоровья |
| `python -m pytest` | Запуск тестов |
| `python -m src.queue worker` | Запуск воркера очереди (локально) |
| `python -m src.telegram_bot` | Запуск Telegram-бота (локально) |
| `python scripts/preflight.py` | Проверить конфигурацию до запуска (код 0/1/2) |
| `python scripts/preflight.py --network` | Плюс Redis, PostgreSQL и целевые сайты |
| `python scripts/qr_login.py --url <сайт> --success-selector <css>` | Ручной вход по QR в профиль |

## Старт: сначала проверка, потом запуск

Порядок обязателен: `preflight.py` до первого запуска называет незаполненные
пункты по именам, поэтому «бот не поднялся» не превращается в чтение логов.

```bash
python scripts/preflight.py             # конфигурация: 0 — ок, 1 — не хватает, 2 — проверить нечем
python scripts/preflight.py --network   # + Redis, PostgreSQL, ozon.ru/avito.ru/wildberries.ru
```

Код `2` — это **не** «всё хорошо»: значит, проверить нечем (нет пакета или файла).
Молчание не считается доказательством.

### Сессия кабинета истекла

**Симптомы:** задача падает на странице входа, в `evidence/` скриншот с формой входа.

**Решение:** снова войти руками — сессия живёт в профиле Chromium и не
восстанавливается кодом.

```bash
python scripts/qr_login.py --url https://www.ozon.ru \
    --success-selector "<селектор кабинета>" --show
```

Коды: `0` — вход подтверждён, `1` — не дождались, `2` — критерий не задан
(подтверждать нечем). Скриншоты каждого шага — в `evidence/qr-login/`.

### Профиль зашифрован, а шифрование выключено

**Симптом:** в логе `Профиль ... зашифрован, хотя BROWSER_PROFILE_ENCRYPTION выключен`.
Это не ошибка: профиль расшифрован для запуска. Предупреждение говорит, что
флаг выключен и после остановки профиль останется обычным. Включите
`BROWSER_PROFILE_ENCRYPTION=true`, если профиль копируется в бэкап.

## Инциденты

### 1. RedisDown — очередь не работает

**Симптомы:**
- `curl /health` возвращает `"redis": false`
- Задачи не принимаются, бот отвечает ошибкой

**Диагностика:**
```bash
docker compose -f docker-compose.prod.yml logs redis
docker compose -f docker-compose.prod.yml exec redis redis-cli -a ${REDIS_PASSWORD} ping
```

**Решение:**
```bash
docker compose -f docker-compose.prod.yml restart redis
```

После перезапуска Redis задачи в очереди сохраняются (данные в volume `redis_data`).

### 2. PostgreSQLDown — outbox не работает

**Симптомы:**
- `curl /health` возвращает `"postgres": false`
- События outbox не сохраняются

**Диагностика:**
```bash
docker compose -f docker-compose.prod.yml logs postgres
docker compose -f docker-compose.prod.yml exec postgres pg_isready -U ${DB_USER} -d ${DB_NAME}
```

**Решение:**
```bash
docker compose -f docker-compose.prod.yml restart postgres
```

### 3. BrowserCrash — браузер не запускается

**Симптомы:**
- Ошибки при запуске `BrowserSession`
- Задачи с типом "browser" падают

**Диагностика:**
```bash
docker compose -f docker-compose.prod.yml logs mvp-browser-agent
```

**Возможные причины:**
- Не хватает системных библиотек Playwright
- Права на директорию профиля не `0700`
- Не хватает памяти

**Решение:**
```bash
# Проверить права на профиль
ls -la browser-profile/
# Должно быть drwx------ (0700)

# Пересобрать образ с Playwright зависимостями
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d
```

### 4. CaptchaRequired — капча на сайте маркетплейса

**Симптомы:**
- Задача со сканированием/входом в кабинет застревает
- В логах: "captcha required" или "капча"

**Решение:**
- Капча проходит только вручную с телефона заказчика
- После ручного входа сессия сохраняется в профиле Chromium
- Следующая задача использует сохранённую сессию

**В runbook:** «Капча не обходится автоматически. Ручной вход с телефона один раз — дальше сессия жива.»

### 5. TaskStuck — задача застряла в очереди

**Диагностика:**
```bash
# Проверить DLQ
docker compose -f docker-compose.prod.yml exec mvp-browser-agent python -m src.queue stats
```

**Решение:**
```bash
# Вернуть задачу из DLQ в очередь
python -m src.queue dlq-replay <task_id>
```

### 6. BotDown — Telegram-бот не отвечает

**Диагностика:**
```bash
docker compose -f docker-compose.prod.yml logs mvp-browser-agent
```

**Проверка токена:**
- `BOT_TOKEN` должен быть установлен в `.env`
- `BOT_ADMIN_ID` должен совпадать с ID пользователя в Telegram

**Решение:**
```bash
docker compose -f docker-compose.prod.yml restart mvp-browser-agent
```

## Мониторинг

### Метрики Prometheus
- `http://localhost:9090/metrics` — все метрики
- `mvp_agent_updates_total` — количество обновлений
- `mvp_agent_errors_total` — количество ошибок по типам
- `mvp_agent_handler_duration_seconds` — длительность обработки

### Health check
```bash
curl -s http://localhost:9090/health | python3 -m json.tool
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

Если `"status": "degraded"` — одна из зависимостей недоступна.

## Бэкапы

### Redis
Данные хранятся в volume `redis_data`. Для бэкапа:
```bash
docker compose -f docker-compose.prod.yml exec redis redis-cli -a ${REDIS_PASSWORD} BGSAVE
docker cp <container_id>:/data/dump.rdb ./redis-backup.rdb
```

### PostgreSQL
```bash
docker compose -f docker-compose.prod.yml exec postgres pg_dump -U ${DB_USER} ${DB_NAME} > ./db-backup.sql
```

### Evidence (скриншоты, видео)
```bash
tar czf ./evidence-backup.tar.gz ./evidence/
```

## Восстановление после сбоя

1. Восстановить Redis из бэкапа (заменить `dump.rdb` в volume)
2. Восстановить PostgreSQL из дампа (`psql -f db-backup.sql`)
3. Перезапустить все сервисы:
   ```bash
   docker compose -f docker-compose.prod.yml up -d
   ```

## Безопасность

- Секреты хранятся в `.env` (не коммитить в git)
- Профиль браузера в `browser-profile/` (0700, не коммитить)
- `BOT_ADMIN_ID` ограничивает доступ к боту только владельцу
- Все необратимые действия требуют подтверждения в Telegram

## Контакты

- Исполнитель: через Telegram (задачи создаются только от BOT_ADMIN_ID)
- Очередь ошибок: DLQ в Redis, ключ `queue:dead`
