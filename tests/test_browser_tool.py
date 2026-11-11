"""Тесты для браузерного инструмента."""

import pytest

from src.browser_tool import BrowserTool


class MockPage:
    """Мок страницы Playwright."""

    def __init__(self):
        self.title_text = "Тестовая страница"
        self.url = "https://example.com"
        self.fill_called = False
        self.click_called = False
        self.screenshot_called = False
        self.last_selector = None
        self.last_value = None

    async def title(self):
        return self.title_text

    async def fill(self, selector, value, timeout=None):
        self.fill_called = True
        self.last_selector = selector
        self.last_value = value

    async def click(self, selector, timeout=None):
        self.click_called = True
        self.last_selector = selector

    async def wait_for_selector(self, selector, timeout=None):
        pass

    async def screenshot(self, path, full_page=True):
        self.screenshot_called = True
        self.screenshot_path = path


class MockBrowserSession:
    """Мок сессии браузера."""

    def __init__(self):
        self._started = True
        self._page = MockPage()
        self.profile_dir = "./browser-profile"
        self.headless = True
        self.viewport = {"width": 1280, "height": 800}
        self.navigation_timeout_ms = 30000
        self.action_timeout_ms = 10000

    @property
    def page(self):
        return self._page

    @property
    def is_started(self):
        return self._started

    async def start(self):
        self._started = True

    async def navigate(self, url):
        self._page.url = url
        return self._page


class TestBrowserTool:
    """Тесты для BrowserTool."""

    def test_init(self):
        """Тест инициализации инструмента."""
        session = MockBrowserSession()
        tool = BrowserTool(session)
        assert tool.session == session

    @pytest.mark.asyncio
    async def test_open_page(self):
        """Тест открытия страницы."""
        session = MockBrowserSession()
        tool = BrowserTool(session)

        result = await tool.call("открыть: https://example.com", "test-task")

        assert "Открыт https://example.com" in result
        assert session._page.url == "https://example.com"

    @pytest.mark.asyncio
    async def test_fill_field(self):
        """Тест заполнения поля."""
        session = MockBrowserSession()
        tool = BrowserTool(session)
        await session.start()

        await tool.call("открыть: https://example.com", "test-task")
        result = await tool.call("заполнить: input[name='email']: test@test.com", "test-task")

        assert "Заполнено поле" in result
        assert session._page.fill_called
        assert session._page.last_selector == "input[name='email']"
        assert session._page.last_value == "test@test.com"

    @pytest.mark.asyncio
    async def test_click_element(self):
        """Тест клика по элементу."""
        session = MockBrowserSession()
        tool = BrowserTool(session)
        await session.start()

        await tool.call("открыть: https://example.com", "test-task")
        result = await tool.call("кликнуть: button.submit", "test-task")

        assert "Клик по элементу" in result
        assert session._page.click_called
        assert session._page.last_selector == "button.submit"

    @pytest.mark.asyncio
    async def test_take_screenshot(self):
        """Тест снятия скриншота."""
        session = MockBrowserSession()
        tool = BrowserTool(session)
        await session.start()

        await tool.call("открыть: https://example.com", "test-task")
        result = await tool.call("скриншот: ", "test-task")

        assert "Скриншот сохранён" in result
        assert session._page.screenshot_called

    @pytest.mark.asyncio
    async def test_get_page_title(self):
        """Тест получения заголовка страницы."""
        session = MockBrowserSession()
        tool = BrowserTool(session)
        await session.start()

        await tool.call("открыть: https://example.com", "test-task")
        result = await tool.call("заголовок: ", "test-task")

        assert "Тестовая страница" in result

    @pytest.mark.asyncio
    async def test_dangerous_selector_blocked(self):
        """Тест блокировки опасных селекторов."""
        session = MockBrowserSession()
        tool = BrowserTool(session)
        await session.start()

        await tool.call("открыть: https://example.com", "test-task")
        result = await tool.call("кликнуть: button[type=submit]", "test-task")

        assert "Блокировка действия" in result

    @pytest.mark.asyncio
    async def test_no_page_error(self):
        """Тест ошибки при отсутствии страницы."""
        session = MockBrowserSession()
        session._page = None
        tool = BrowserTool(session)
        # Браузер «запущен», но страница не открыта

        result = await tool.call("скриншот: ", "test-task")
        assert "Нет открытой страницы" in result
