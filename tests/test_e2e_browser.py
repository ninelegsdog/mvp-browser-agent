"""E2E тесты с Playwright для браузерной автоматизации.

Тесты проверяют базовый функционал браузера: запуск, навигацию, скриншоты.
Запускаются с браузером Chromium в headless режиме.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.browser import BrowserSession


@pytest.fixture
async def browser_session():
    """Фикстура браузера для E2E тестов."""
    session = BrowserSession(headless=True)
    await session.start()
    yield session
    await session.close()


@pytest.mark.asyncio
async def test_browser_start_and_close(browser_session):
    """Тест запуска и остановки браузера."""
    assert browser_session.is_started is True
    assert browser_session._context is not None
    assert browser_session._page is not None


@pytest.mark.asyncio
async def test_browser_navigate_to_example(browser_session):
    """Тест навигации на example.com."""
    page = await browser_session.navigate("https://example.com")
    title = await page.title()
    assert "Example" in title
    await page.close()


@pytest.mark.asyncio
async def test_browser_check_session(browser_session):
    """Тест проверки сессии."""
    # На новой странице сессия может быть не живой, но браузер должен работать
    result = await browser_session.check_session(timeout_ms=5000)
    # Не проверяем конкретный результат, так как зависит от сайта
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_browser_health_check(browser_session):
    """Тест проверки здоровья сайтов."""
    results = await browser_session.health_check_sites()
    assert isinstance(results, dict)
    # Может быть пустым, если сайты недоступны в тестовой среде


@pytest.mark.asyncio
async def test_browser_create_isolated_context(browser_session):
    """Тест создания изолированного контекста."""
    context = await browser_session.create_isolated_context("test1")
    assert context is not None
    context2 = await browser_session.get_context("test1")
    assert context2 is context
    await browser_session.close_context("test1")
    assert "test1" not in browser_session._contexts


@pytest.mark.asyncio
async def test_browser_screenshot(browser_session):
    """Тест снятия скриншота."""
    page = await browser_session.navigate("https://example.com")
    await page.screenshot(path="/tmp/test_screenshot.png")
    assert os.path.exists("/tmp/test_screenshot.png")
    await page.close()


@pytest.mark.asyncio
async def test_smart_locator(browser_session):
    """Тест SmartLocator - создание экземпляра."""
    from src.smart_locator import SmartLocator

    page = await browser_session.navigate("https://example.com")
    locator = SmartLocator(page, timeout_ms=5000)
    assert locator is not None
    assert locator.page is page
    await page.close()
