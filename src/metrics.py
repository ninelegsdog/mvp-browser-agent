"""Prometheus RED метрики для агента и очереди.

Паттерн botkit-core/metrics.py:
- Счётчики обновлений и ошибок с лейблами.
- Гистограммы длительности обработки.
- Эндпоинт /metrics для Prometheus scrape.
- /health с проверкой зависимостей (Redis, PostgreSQL).
"""

import logging
from aiohttp import web

try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
except ImportError:  # pragma: no cover - prometheus_client может отсутствовать в тестах
    Counter = Histogram = None  # type: ignore

    def generate_latest():
        return b""

    CONTENT_TYPE_LATEST = "text/plain"


logger = logging.getLogger(__name__)

if Counter:
    UPDATES_TOTAL = Counter(
        "mvp_agent_updates_total", "Всего обновлений", ["type"]
    )
    ERRORS_TOTAL = Counter(
        "mvp_agent_errors_total", "Всего ошибок", ["error_type"]
    )
    HANDLER_DURATION = Histogram(
        "mvp_agent_handler_duration_seconds", "Длительность обработки"
    )
    QUEUE_DEPTH = Counter(
        "mvp_agent_queue_depth", "Текущая глубина очереди", ["queue"]
    )
    RETRY_COUNT = Histogram(
        "mvp_agent_retry_count", "Количество повторов задачи", ["queue", "error_category"]
    )
else:
    UPDATES_TOTAL = ERRORS_TOTAL = HANDLER_DURATION = QUEUE_DEPTH = RETRY_COUNT = None


async def metrics_handler(request):
    if generate_latest:
        return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST)
    return web.Response(text="prometheus_client not installed", status=503)


async def health_handler(request):
    """Health check с проверкой зависимостей."""
    checks = {"redis": False, "postgres": False}
    overall = "ok"

    # Проверка Redis
    try:
        import redis.asyncio as redis_async

        r = redis_async.from_url(settings.REDIS_URL, decode_responses=True)
        await r.ping()
        checks["redis"] = True
        await r.aclose()
    except Exception as e:
        checks["redis"] = False
        overall = "degraded"
        logger.warning("Redis health check failed: %s", e)

    # Проверка PostgreSQL
    try:
        import asyncpg

        conn = await asyncpg.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.DB_NAME,
            timeout=5,
        )
        await conn.fetch("SELECT 1")
        checks["postgres"] = True
        await conn.close()
    except Exception as e:
        checks["postgres"] = False
        overall = "degraded"
        logger.warning("PostgreSQL health check failed: %s", e)

    status_code = 200 if overall == "ok" else 503
    return web.json_response({"status": overall, "checks": checks}, status=status_code)


def create_metrics_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_get("/health", health_handler)
    return app


async def start_metrics_server(port: int = 9090):
    app = create_metrics_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Metrics server started on port %d", port)
    return runner
