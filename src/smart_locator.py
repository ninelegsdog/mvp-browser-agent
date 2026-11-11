"""SmartLocator — селекторы с приоритетом для надёжного поиска элементов.

Приоритет (от наиболее надёжного к наименее):
1. ARIA role + имя (data-testid, aria-label, role=button[name=...])
2. Текстовое содержимое (text=..., has-text=...)
3. CSS-селектор (класс, ID, атрибуты)

Каждый селектор пробуется по очереди с коротким таймаутом.
Первый Successful элемент используется.
"""

import logging

from playwright.async_api import Locator, Page

logger = logging.getLogger(__name__)


class SmartLocator:
    """Поиск элемента с перебором стратегий селекторов.

    Поддерживает self-healing: запоминает успешные селекторы и использует их
    первыми при следующих вызовах.
    """

    def __init__(self, page: Page, timeout_ms: int = 5000):
        self.page = page
        self.timeout_ms = timeout_ms
        self._successful_selectors: dict[str, list[str]] = {}

    async def locate(self, element_key: str, *strategies: str) -> Locator | None:
        """Попробовать стратегии селекторов по очереди.

        Пример:
            locator = SmartLocator(page, timeout_ms=3000)
            element = await locator.locate(
                'submit_button',
                'role=button[name="Отправить"]',
                'text=Отправить',
                '[data-testid="submit-btn"]',
                'button.submit',
            )
        """
        # Сначала пробуем ранее успешные селекторы для этого элемента
        cached = self._successful_selectors.get(element_key, [])
        all_strategies = cached + [s for s in strategies if s not in cached]
        if not all_strategies:
            logger.warning("Нет ни одной стратегии для элемента %s", element_key)
            return None

        per_strategy_timeout = max(self.timeout_ms // len(all_strategies), 500)

        for strategy in all_strategies:
            try:
                locator = self.page.locator(strategy).first
                await locator.wait_for(state="visible", timeout=per_strategy_timeout)
            except Exception as e:
                logger.debug("Селектор не сработал: %s (%s)", strategy, e)
                continue

            # Запомнить успешный селектор (self-healing), держим не больше 5
            known = self._successful_selectors.setdefault(element_key, [])
            if strategy in known:
                known.remove(strategy)
            known.insert(0, strategy)
            del known[5:]
            logger.info("Селектор найден: %s (элемент: %s)", strategy, element_key)
            return locator

        logger.warning("Ни один селектор не сработал для %s из %d", element_key, len(all_strategies))
        return None

    async def click(self, element_key: str, *strategies: str) -> bool:
        """Найти элемент и кликнуть по нему."""
        locator = await self.locate(element_key, *strategies)
        if locator is None:
            return False
        try:
            await locator.click(timeout=self.timeout_ms)
            logger.info("Клик выполнен по элементу %s", element_key)
            return True
        except Exception as e:
            logger.warning("Клик не удался: %s", e)
            return False

    async def fill(self, value: str, element_key: str, *strategies: str) -> bool:
        """Найти поле и заполнить значением."""
        locator = await self.locate(element_key, *strategies)
        if locator is None:
            return False
        try:
            await locator.fill(value, timeout=self.timeout_ms)
            logger.info("Поле %s заполнено: %s", element_key, value[:50])
            return True
        except Exception as e:
            logger.warning("Заполнение не удалось: %s", e)
            return False

    async def wait_for(self, element_key: str, *strategies: str) -> Locator | None:
        """Дождаться появления любого из селекторов."""
        return await self.locate(element_key, *strategies)


# Удобные фабрики для частых случаев


def submit_button_selectors(button_name: str) -> list[str]:
    """Селекторы для кнопки отправки формы."""
    return [
        f'role=button[name="{button_name}"]',
        f'button:has-text("{button_name}")',
        f'[data-testid="submit-{button_name.lower()}"]',
        'button[type="submit"]',
    ]


def input_selectors(label: str) -> list[str]:
    """Селекторы для поля ввода по метке."""
    return [
        f'role=textbox[name="{label}"]',
        f'input[placeholder="{label}"]',
        f'input[aria-label="{label}"]',
        f'[data-testid="input-{label.lower()}"]',
        f'input[name="{label.lower()}"]',
    ]


def link_selectors(text: str) -> list[str]:
    """Селекторы для ссылки по тексту."""
    return [
        f'role=link[name="{text}"]',
        f'a:has-text("{text}")',
        f'[data-testid="link-{text.lower()}"]',
    ]
