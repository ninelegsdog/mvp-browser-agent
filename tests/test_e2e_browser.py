"""E2E тесты с Playwright для браузерной автоматизации.

Тесты проверяют базовый функционал браузера: запуск, навигацию, скриншоты.
Запускаются с браузером Chromium в headless режиме.

Сеть не используется: страница отдаётся из локального файла. Тест, который
ходит на example.com, проверяет доступность интернета, а не наш код, и падает
или висит там, где сети нет.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.browser import BrowserSession


@pytest.fixture
def local_page_url(tmp_path):
    """Локальная страница вместо внешнего сайта: сеть не нужна."""
    page = tmp_path / "page.html"
    page.write_text(
        "<!doctype html><html><head><title>Example page</title></head>"
        "<body><h1>Локальная страница</h1><p>Проверка браузера</p></body></html>",
        encoding="utf-8",
    )
    return page.as_uri()


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
async def test_browser_navigates_to_local_page(browser_session, local_page_url):
    """Навигация открывает нужную страницу и отдаёт её заголовок."""
    page = await browser_session.navigate(local_page_url)
    title = await page.title()
    assert title == "Example page"
    await page.close()


@pytest.mark.asyncio
async def test_browser_check_session_sees_present_and_absent_selector(browser_session, local_page_url):
    """Проверка сессии различает «элемент есть» и «элемента нет»."""
    await browser_session.navigate(local_page_url)

    assert await browser_session.check_session(selector="h1", timeout_ms=2000) is True
    assert await browser_session.check_session(selector="#missing", timeout_ms=500) is False


@pytest.mark.asyncio
async def test_browser_health_check_marks_reachable_and_unreachable(browser_session, local_page_url, monkeypatch):
    """Проверка сайтов различает доступный адрес и закрытый порт."""
    from src import browser as browser_module

    monkeypatch.setattr(browser_module, "TARGET_SITES", (local_page_url, "http://127.0.0.1:9"))
    results = await browser_session.health_check_sites()

    assert results[local_page_url] is True
    assert results["http://127.0.0.1:9"] is False


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
async def test_browser_screenshot(browser_session, local_page_url, tmp_path):
    """Скриншот сохраняется и не пустой."""
    page = await browser_session.navigate(local_page_url)
    path = tmp_path / "screenshot.png"
    await page.screenshot(path=str(path))
    assert path.exists()
    assert path.stat().st_size > 0
    await page.close()


@pytest.mark.asyncio
async def test_profile_not_encrypted_by_default(tmp_path):
    """По умолчанию профиль остаётся обычным: шифрование — отдельный флаг."""
    from src.browser_encryption import is_profile_encrypted

    profile = tmp_path / "profile"
    session = BrowserSession(profile_dir=str(profile), headless=True)
    await session.start()
    await session.close()

    assert is_profile_encrypted(profile) is False


@pytest.mark.asyncio
async def test_profile_encryption_roundtrip_through_session(tmp_path, monkeypatch):
    """Включённое шифрование: закрыли — зашифрован, запустили — расшифрован."""
    from src.browser_encryption import is_profile_encrypted
    from src.config import settings

    monkeypatch.setattr(settings, "BROWSER_PROFILE_ENCRYPTION", True)
    profile = tmp_path / "profile"

    first = BrowserSession(profile_dir=str(profile), headless=True)
    await first.start()
    await first.close()
    assert is_profile_encrypted(profile) is True

    second = BrowserSession(profile_dir=str(profile), headless=True)
    await second.start()
    assert is_profile_encrypted(profile) is False
    assert second.is_started is True
    await second.close()
    assert is_profile_encrypted(profile) is True


@pytest.mark.asyncio
async def test_smart_locator(browser_session, local_page_url):
    """Тест SmartLocator - создание экземпляра."""
    from src.smart_locator import SmartLocator

    page = await browser_session.navigate(local_page_url)
    locator = SmartLocator(page, timeout_ms=5000)
    assert locator is not None
    assert locator.page is page
    await page.close()
