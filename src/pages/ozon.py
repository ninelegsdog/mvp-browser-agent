"""Page Object Model для Ozon.ru.

Инкапсулирует селекторы и действия для поиска товара и оформления заказа.
"""

from typing import Optional

from playwright.async_api import Page


class OzonPage:
    """Страница Ozon с базовыми действиями."""

    URL = "https://www.ozon.ru"

    # Селекторы (приоритет: role → label → text → data-testid → CSS)
    SEARCH_INPUT = 'input[name="text"]'
    SEARCH_BUTTON = 'button[type="submit"]'
    PRODUCT_CARD = '[data-testid="product-card"]'
    ADD_TO_CART_BUTTON = 'button:has-text("В корзину")'
    CART_BUTTON = 'a[href="/cart"]'
    CHECKOUT_BUTTON = 'button:has-text("Оформить заказ")'

    def __init__(self, page):
        self.page = page

    async def goto(self):
        await self.page.goto(self.URL, wait_until="domcontentloaded")

    async def search(self, query: str):
        await self.page.fill(self.SEARCH_INPUT, query)
        await self.page.click(self.SEARCH_BUTTON)
        await self.page.wait_for_timeout(2000)

    async def first_product(self) -> Optional[str]:
        """Вернуть URL первого товара или None."""
        try:
            await self.page.wait_for_selector(self.PRODUCT_CARD, timeout=5000)
            element = self.page.locator(self.PRODUCT_CARD).first
            link = element.locator("a").first
            href = await link.get_attribute("href")
            return href
        except Exception:
            return None

    async def add_first_to_cart(self) -> bool:
        """Добавить первый товар в корзину."""
        try:
            await self.page.click(self.ADD_TO_CART_BUTTON)
            return True
        except Exception:
            return False

    async def open_cart(self):
        await self.page.click(self.CART_BUTTON)

    async def checkout(self) -> bool:
        """Нажать «Оформить заказ». Возвращает True если кнопка есть."""
        try:
            await self.page.click(self.CHECKOUT_BUTTON)
            return True
        except Exception:
            return False
