"""Тесты SmartLocator: перебор селекторов, self-healing, клик и ввод."""

import pytest

from src.smart_locator import SmartLocator


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str):
        self.page = page
        self.selector = selector
        self.clicked = False
        self.filled_with = None

    @property
    def first(self) -> "FakeLocator":
        """Как в Playwright: .first возвращает локатор первого совпадения."""
        return self

    async def wait_for(self, state: str, timeout: int):
        self.page.waits.append((self.selector, state, timeout))
        if self.selector not in self.page.visible:
            raise TimeoutError(f"{self.selector} not visible")

    async def click(self, timeout: int):
        if self.selector not in self.page.visible:
            raise TimeoutError("not visible")
        self.clicked = True
        self.page.clicks.append(self.selector)

    async def fill(self, value: str, timeout: int):
        if self.selector not in self.page.visible:
            raise TimeoutError("not visible")
        self.filled_with = value
        self.page.fills.append((self.selector, value))


class FakePage:
    def __init__(self, visible: set[str]):
        self.visible = visible
        self.waits: list[tuple[str, str, int]] = []
        self.clicks: list[str] = []
        self.fills: list[tuple[str, str]] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


@pytest.mark.asyncio
async def test_locate_returns_first_working_strategy():
    page = FakePage(visible={"button.submit"})
    loc = SmartLocator(page, timeout_ms=3000)

    found = await loc.locate("submit", "#missing", "button.submit", "button.other")

    assert found is not None
    assert found.selector == "button.submit"


@pytest.mark.asyncio
async def test_locate_returns_none_when_nothing_works():
    page = FakePage(visible=set())
    loc = SmartLocator(page, timeout_ms=1000)

    assert await loc.locate("submit", "#a", "#b") is None


@pytest.mark.asyncio
async def test_successful_selector_is_cached_and_tried_first():
    page = FakePage(visible={"button.submit"})
    loc = SmartLocator(page, timeout_ms=3000)

    await loc.locate("submit", "#wrong", "button.submit")
    page.waits.clear()
    await loc.locate("submit", "#wrong", "button.submit")

    # Кэшированный селектор пробуется первым и сразу срабатывает
    assert page.waits[0][0] == "button.submit"
    assert loc._successful_selectors["submit"] == ["button.submit"]


@pytest.mark.asyncio
async def test_cache_is_limited_to_five_selectors():
    page = FakePage(visible={"s1", "s2", "s3", "s4", "s5", "s6"})
    loc = SmartLocator(page)

    for selector in ("s1", "s2", "s3", "s4", "s5", "s6"):
        page.visible = {selector}
        await loc.locate("element", selector)

    assert len(loc._successful_selectors["element"]) == 5
    assert loc._successful_selectors["element"][0] == "s6"


@pytest.mark.asyncio
async def test_click_uses_found_locator():
    page = FakePage(visible={"a.link"})
    loc = SmartLocator(page)

    assert await loc.click("link", "a.link") is True
    assert page.clicks == ["a.link"]


@pytest.mark.asyncio
async def test_fill_passes_value_to_field():
    page = FakePage(visible={"input[name=email]"})
    loc = SmartLocator(page)

    assert await loc.fill("me@example.com", "email", "input[name=email]") is True
    assert page.fills == [("input[name=email]", "me@example.com")]


@pytest.mark.asyncio
async def test_click_returns_false_when_not_found():
    page = FakePage(visible=set())
    loc = SmartLocator(page, timeout_ms=100)

    assert await loc.click("link", "a.missing") is False
