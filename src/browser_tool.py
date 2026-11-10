"""Реальный инструмент браузера для агента.

Использует Playwright с постоянным профилем Chromium для выполнения действий
в браузере: открытие страниц, скриншоты, навигация. Все действия фиксируются
как доказательство (схема defect.v1).
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from src.browser import BrowserSession
from src.config import settings
from src.proof import EvidenceRecorder
from src.security import assert_no_secrets_in_text

logger = logging.getLogger(__name__)


class BrowserTool:
    """Инструмент браузера с реальным Playwright сессией."""

    def __init__(self, session: BrowserSession):
        self.session = session

    async def call(self, input: str, task_id: str) -> str:
        """Выполнить действие в браузере.

        Формат input: "действие: цель"
        Например: "открыть: https://ozon.ru" или "скриншот: текущая страница"
        """
        assert_no_secrets_in_text(input, f"browser_tool.{task_id}")

        if not self.session._browser:
            await self.session.start()

        parts = input.split(":", 1)
        if len(parts) == 2:
            action, target = parts[0].strip(), parts[1].strip()
        else:
            action, target = "открыть", input.strip()

        try:
            if action in ("открыть", "перейти", "goto"):
                page = await self.session.navigate(target)
                title = await page.title()
                # Автоматический скриншот после открытия как доказательство
                meta = await self._capture_evidence(page, task_id, f"open_{target.replace('://', '_').replace('/', '_')[:40]}")
                return f"Открыт {target} (заголовок: {title}). Скриншот: {meta['screenshot']}"

            elif action in ("скриншот", "screenshot"):
                page = self.session.page
                meta = await self._capture_evidence(page, task_id, "screenshot")
                return f"Скриншот сохранён: {meta['screenshot']}"

            elif action == "заголовок":
                page = self.session.page
                title = await page.title()
                return f"Заголовок страницы: {title}"

            else:
                return f"Неизвестное действие: {action}. Доступные: открыть, скриншот, заголовок"

        except Exception as e:
            logger.error(f"Ошибка браузера: {e}")
            return f"Ошибка браузера: {str(e)}"

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