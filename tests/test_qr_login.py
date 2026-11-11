"""Тесты вспомогательной логики входа по QR.

Сам браузер здесь не запускается: проверяем имена скриншотов и правило
подтверждения входа — именно оно решает, скажет ли скрипт «вошли» без оснований.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("qr_login", ROOT / "scripts/qr_login.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["qr_login"] = module
    spec.loader.exec_module(module)
    return module


qr_login = _load()


class FakePage:
    """Страница-двойник: умеет только то, что нужно проверке."""

    def __init__(self, url="https://example.com", selector_found=True):
        self.url = url
        self.selector_found = selector_found
        self.requested = []

    async def wait_for_selector(self, selector, timeout):
        self.requested.append((selector, timeout))
        if not self.selector_found:
            raise TimeoutError("не появился")


def test_screenshot_name_keeps_order_and_site():
    assert qr_login.screenshot_name("https://www.ozon.ru", 1) == "www.ozon.ru-001.png"
    assert qr_login.screenshot_name("https://www.ozon.ru", 12) == "www.ozon.ru-012.png"


def test_screenshot_name_removes_path_separators():
    name = qr_login.screenshot_name("https://example.com/login?next=/a/b", 3)

    assert "/" not in name
    assert "?" not in name
    assert name.endswith("-003.png")


def test_screenshot_name_has_fallback_for_empty_url():
    assert qr_login.screenshot_name("", 0) == "login-000.png"


@pytest.mark.asyncio
async def test_confirmed_when_selector_appears():
    page = FakePage()

    assert await qr_login._is_confirmed(page, "[data-profile]", None, wait_seconds=2) is True
    assert page.requested == [("[data-profile]", 2000)]


@pytest.mark.asyncio
async def test_not_confirmed_when_selector_missing():
    page = FakePage(selector_found=False)

    assert await qr_login._is_confirmed(page, "[data-profile]", None, wait_seconds=2) is False


@pytest.mark.asyncio
async def test_confirmed_by_url_substring():
    page = FakePage(url="https://www.ozon.ru/my/profile")

    assert await qr_login._is_confirmed(page, None, "/my/profile", wait_seconds=1) is True
    assert await qr_login._is_confirmed(page, None, "/login", wait_seconds=1) is False


@pytest.mark.asyncio
async def test_without_criterion_never_claims_success():
    """Скрипт не должен говорить «вошли», если проверять нечем."""
    page = FakePage()

    assert await qr_login._is_confirmed(page, None, None, wait_seconds=1) is False
