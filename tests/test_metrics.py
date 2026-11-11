"""Смоук-тесты Prometheus-эндпоинтов и SLO-расчётов.

Модуль metrics.py раньше падал на импорте (NameError: settings), поэтому
эти тесты в первую очередь фиксируют, что модуль собирается и отдаёт ответы.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src import metrics


@pytest.fixture
async def client():
    app = metrics.create_metrics_app()
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_module_imports_and_app_builds():
    app = metrics.create_metrics_app()
    assert isinstance(app, web.Application)
    routes = {route.resource.canonical for route in app.router.routes()}
    assert {"/metrics", "/health", "/live", "/ready", "/alerts", "/slo", "/memory"} <= routes


async def test_liveness_returns_alive(client):
    response = await client.get("/live")
    assert response.status == 200
    assert await response.json() == {"status": "alive"}


async def test_slo_returns_targets(client):
    response = await client.get("/slo")
    body = await response.json()
    assert response.status == 200
    assert body["uptime_target"] == metrics.SLO_UPTIME_TARGET
    assert body["latency_p95_target"] == metrics.SLO_LATENCY_P95_TARGET


async def test_alerts_ok_when_no_data(client):
    """Без накопленных метрик алертов нет — эндпоинт отвечает 200."""
    response = await client.get("/alerts")
    assert response.status == 200
    assert await response.json() == {"status": "ok", "alerts": []}


async def test_memory_endpoint_starts_tracing(client):
    response = await client.get("/memory")
    body = await response.json()
    assert response.status == 200
    assert body["current_bytes"] >= 0
    assert body["peak_bytes"] >= body["current_bytes"]


def test_success_rate_none_without_metrics():
    """При отсутствии prometheus_client счётчиков метрика недоступна."""
    if metrics.TASK_SUCCESS_TOTAL is None:
        assert metrics.get_success_rate() is None
        assert metrics.check_success_rate_alert() is None
    else:
        assert metrics.get_success_rate() is None  # нет наблюдений — нет значения
