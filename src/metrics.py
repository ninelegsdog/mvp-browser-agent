"""Prometheus RED метрики для агента и очереди.

Паттерн botkit-core/metrics.py:
- Счётчики обновлений и ошибок с лейблами.
- Гистограммы длительности обработки.
- Эндпоинт /metrics для Prometheus scrape.
- /health с проверкой зависимостей (Redis, PostgreSQL).
"""

import logging
from typing import Any, Dict, Optional

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
    TASK_SUCCESS_TOTAL = Counter(
        "mvp_agent_task_success_total", "Успешные задачи", ["type"]
    )
    TASK_FAILURE_TOTAL = Counter(
        "mvp_agent_task_failure_total", "Неуспешные задачи", ["type", "error_category"]
    )
else:
    UPDATES_TOTAL = ERRORS_TOTAL = HANDLER_DURATION = QUEUE_DEPTH = RETRY_COUNT = TASK_SUCCESS_TOTAL = TASK_FAILURE_TOTAL = None


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


async def alerts_handler(request):
    """Эндпоинт для проверки алертов."""
    alert = check_success_rate_alert()
    if alert:
        return web.json_response(alert, status=503)
    return web.json_response({"status": "ok", "alerts": []})


async def liveness_handler(request):
    """Liveness probe - приложение живо."""
    return web.json_response({"status": "alive"})


async def readiness_handler(request):
    """Readiness probe - приложение готово принимать трафик."""
    checks = {"redis": False, "postgres": False}
    try:
        import redis.asyncio as redis_async
        r = redis_async.from_url(settings.REDIS_URL, decode_responses=True)
        await r.ping()
        checks["redis"] = True
        await r.aclose()
    except Exception:
        pass

    try:
        import asyncpg
        conn = await asyncpg.connect(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            database=settings.DB_NAME,
            timeout=3,
        )
        await conn.fetch("SELECT 1")
        checks["postgres"] = True
        await conn.close()
    except Exception:
        pass

    all_ready = all(checks.values())
    status_code = 200 if all_ready else 503
    return web.json_response({"status": "ready" if all_ready else "not_ready", "checks": checks}, status=status_code)


def create_metrics_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_get("/health", health_handler)
    app.router.add_get("/alerts", alerts_handler)
    app.router.add_get("/live", liveness_handler)
    app.router.add_get("/ready", readiness_handler)
    return app


async def start_metrics_server(port: int = 9090):
    app = create_metrics_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Metrics server started on port %d", port)
    return runner


# --- Алерты ---

SUCCESS_RATE_THRESHOLD = 0.90  # 90% успешных задач


def get_success_rate() -> Optional[float]:
    """Рассчитать successful rate на основе накопленных счётчиков."""
    if TASK_SUCCESS_TOTAL is None or TASK_FAILURE_TOTAL is None:
        return None
    try:
        success = sum(
            s.value for s in TASK_SUCCESS_TOTAL._metrics.values()
        ) if hasattr(TASK_SUCCESS_TOTAL, '_metrics') else 0
        failure = sum(
            s.value for s in TASK_FAILURE_TOTAL._metrics.values()
        ) if hasattr(TASK_FAILURE_TOTAL, '_metrics') else 0
        total = success + failure
        if total == 0:
            return None
        return success / total
    except Exception:
        return None


def check_success_rate_alert() -> Optional[Dict[str, Any]]:
    """Проверить successful rate и вернуть алерт если ниже порога."""
    rate = get_success_rate()
    if rate is None:
        return None
    if rate < SUCCESS_RATE_THRESHOLD:
        return {
            "alert": "success_rate_below_threshold",
            "current_rate": rate,
            "threshold": SUCCESS_RATE_THRESHOLD,
            "message": f"Successful rate {rate:.2%} below threshold {SUCCESS_RATE_THRESHOLD:.0%}",
        }
    return None
