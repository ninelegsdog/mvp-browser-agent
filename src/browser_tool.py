"""Реальный инструмент браузера для агента.

Использует Playwright с постоянным профилем Chromium для выполнения действий
в браузере: открытие страниц, скриншоты, навигация, ввод текста, клики.
Все действия фиксируются как доказательство (схема defect.v1).
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from playwright.async_api import Page

from src.browser import BrowserSession
from src.config import settings
from src.proof import EvidenceRecorder
from src.security import assert_no_secrets_in_text

logger = logging.getLogger(__name__)


class BrowserTool:
    """Инструмент браузера с постоянным профилем Chromium."""

    def __init__(self, session: BrowserSession):
        self.session = session

    async def call(
        self,
        input: str,
        task_id: str,
        action: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Выполнить действие в браузере.

        Формат input: "действие: цель"
        Например: "открыть: https://ozon.ru" или "скриншот: текущая страница"
        """
        assert_no_secrets_in_text(input, f"browser_tool.{task_id}")

        if not self.session._browser:
            await self.session.start()

        # Парсим вход: может быть "действие: цель" или просто цель
        parts = input.split(":", 1)
        if len(parts) == 2:
            action_str, target = parts[0].strip(), parts[1].strip()
        else:
            action_str, target = "открыть", input.strip()

        # Если передан объект action из планировщика, приоритет у него
        if action is not None:
            action_type = action.get("type", action_str)
            target = action.get("target", target) or target
            action_value = action.get("value")
        else:
            action_type = action_str
            action_value = None

        try:
            if action_type in ("открыть", "перейти", "goto", "open"):
                return await self._open_page(target, task_id)
            elif action_type in ("заполнить", "fill", "input"):
                return await self._fill_field(target, action_value or "", task_id)
            elif action_type in ("кликнуть", "click", "press"):
                return await self._click_element(target, task_id)
            elif action_type in ("прокрутить", "scroll"):
                return await self._scroll_page(target, task_id)
            elif action_type in ("подождать", "wait"):
                return await self._wait_for_element(target, task_id)
            elif action_type in ("прочитать", "read", "get_text"):
                return await self._read_element(target, task_id)
            elif action_type in ("скриншот", "screenshot"):
                return await self._take_screenshot(task_id, prefix="screenshot")
            elif action_type in ("заголовок", "title", "get_title"):
                return await self._get_page_title(task_id)
            elif action_type in ("завершить", "finish", "done"):
                return f"Задача завершена. Результат: {target or 'без деталей'}"
            elif action_type in ("подтвердить_с_пользователем", "confirm_user"):
                return f"Ожидает подтверждения пользователя: {target or 'действие требует подтверждения'}"
            else:
                return f"Неизвестное действие: {action_type}. Доступные: открыть, заполнить, кликнуть, прокрутить, подождать, прочитать, скриншот, заголовок, завершить, подтвердить_с_пользователем"

        except Exception as e:
            logger.error(f"Ошибка браузера при действии {action_type}: {e}")
            return f"Ошибка браузера: {str(e)}"

    async def _open_page(self, url: str, task_id: str) -> str:
        """Открыть страницу по URL."""
        page = await self.session.navigate(url)
        title = await page.title()
        meta = await self._capture_evidence(
            page, task_id, f"open_{url.replace('://', '_').replace('/', '_')[:50]}"
        )
        return f"Открыт {url} (заголовок: {title}). Скриншот: {meta['screenshot']}"

    async def _fill_field(self, selector: str, value: str, task_id: str) -> str:
        """Заполнить поле ввода значением."""
        if not self.session._page:
            raise RuntimeError("Нет открытой страницы. Сначала откройте сайт через действие 'открыть'.")

        page = self.session.page
        await page.fill(selector, value, timeout=self.session.navigation_timeout_ms)
        meta = await self._capture_evidence(page, task_id, f"fill_{selector.replace(':', '_').replace(' ', '_')[:40]}")
        return f"Заполнено поле {selector} значением '{value[:50]}'. Скриншот: {meta['screenshot']}"

    async def _click_element(self, selector: str, task_id: str) -> str:
        """Кликнуть по элементу."""
        if not self.session._page:
            raise RuntimeError("Нет открытой страницы. Сначала откройте сайт через действие 'открыть'.")

        page = self.session.page
        await page.click(selector, timeout=self.session.action_timeout_ms)
        meta = await self._capture_evidence(page, task_id, f"click_{selector.replace(':', '_').replace(' ', '_')[:40]}")
        return f"Клик по элементу {selector}. Скриншот: {meta['screenshot']}"

    async def _scroll_page(self, selector: str, task_id: str) -> str:
        """Прокрутить страницу или элемент."""
        if not self.session._page:
            raise RuntimeError("Нет открытой страницы.")

        page = self.session.page
        await page.evaluate(
            f"() => {{ const el = document.querySelector('{selector}'); if(el) el.scrollIntoView(); else window.scrollBy(0, 500); }}"
        )
        meta = await self._capture_evidence(page, task_id, f"scroll_{selector.replace(':', '_').replace(' ', '_')[:40]}")
        return f"Прокрутка выполнена для {selector}. Скриншот: {meta['screenshot']}"

    async def _wait_for_element(self, selector: str, task_id: str) -> str:
        """Подождать появления элемента на странице."""
        if not self.session._page:
            raise RuntimeError("Нет открытой страницы.")

        page = self.session.page
        await page.wait_for_selector(selector, timeout=self.session.navigation_timeout_ms)
        meta = await self._capture_evidence(page, task_id, f"wait_{selector.replace(':', '_').replace(' ', '_')[:40]}")
        return f"Элемент {selector} появился на странице. Скриншот: {meta['screenshot']}"

    async def _read_element(self, selector: str, task_id: str) -> str:
        """Прочитать текст элемента."""
        if not self.session._page:
            raise RuntimeError("Нет открытой страницы.")

        page = self.session.page
        text_content = await page.locator(selector).first.text_content(timeout=self.session.action_timeout_ms)
        meta = await self._capture_evidence(page, task_id, f"read_{selector.replace(':', '_').replace(' ', '_')[:40]}")
        return f"Текст элемента {selector}: {text_content[:500]}. Скриншот: {meta['screenshot']}"

    async def _take_screenshot(self, task_id: str, prefix: str = "screenshot") -> str:
        """Сделать скриншот текущей страницы."""
        if not self.session._page:
            raise RuntimeError("Нет открытой страницы.")

        page = self.session.page
        meta = await self._capture_evidence(page, task_id, prefix)
        return f"Скриншот сохранён: {meta['screenshot']}"

    async def _get_page_title(self, task_id: str) -> str:
        """Получить заголовок текущей страницы."""
        if not self.session._page:
            raise RuntimeError("Нет открытой страницы.")

        page = self.session.page
        title = await page.title()
        return f"Заголовок страницы: {title}"

    async def _capture_evidence(self, page: Page, task_id: str, prefix: str = "step") -> dict:
        """Снять скриншот и сохранить метаданные как доказательство."""
        evidence_dir = Path(settings.EVIDENCE_DIR) / task_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        screenshot_path = evidence_dir / f"{prefix}.png"
        await page.screenshot(path=str(screenshot_path), full_page=True)

        # Записываем в EvidenceRecorder для отслеживания шагов
        recorder = EvidenceRecorder(task_id)
        recorder.record_step(
            prefix,
            {"url": page.url, "title": await page.title()},
            screenshot_path=str(screenshot_path),
        )

        return {
            "task_id": task_id,
            "prefix": prefix,
            "screenshot": str(screenshot_path),
            "url": page.url,
            "title": await page.title(),
        }