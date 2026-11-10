"""Браузер с сохранённой сессией: Playwright + постоянный профиль Chromium.

Паттерн kwork-packs/tg-web.mjs:
- launchPersistentContext с профилем на диске (сессия живёт между запусками).
- Вход по QR в реальный веб-интерфейс (Telegram Web, маркетплейсы, почта).
- Проверка «сессия жива» перед каждым поручением.
- Профиль 0700, вне git, отдельные профили по аккаунтам.
- Экспорт доказательств: скриншот + видео + trace при сбое.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page

from src.config import settings
from src.security import check_profile_permissions, assert_no_secrets_in_text

TARGET_SITES = ["https://www.ozon.ru", "https://www.avito.ru", "https://www.wildberries.ru"]

logger = logging.getLogger(__name__)


class BrowserSession:
    """Один постоянный профиль Chromium с живой сессией."""

    def __init__(
        self,
        profile_dir: str = None,
        headless: bool = None,
        viewport_width: int = None,
        viewport_height: int = None,
        record_video_dir: str = None,
    ):
        self.profile_dir = profile_dir or settings.BROWSER_PROFILE_DIR
        self.headless = headless if headless is not None else settings.BROWSER_HEADLESS
        self.viewport = {
            "width": viewport_width or settings.BROWSER_VIEWPORT_WIDTH,
            "height": viewport_height or settings.BROWSER_VIEWPORT_HEIGHT,
        }
        self.record_video_dir = record_video_dir
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def start(self) -> None:
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        if not check_profile_permissions(self.profile_dir):
            logger.warning("Profile dir %s is not 0700 — fixing", self.profile_dir)
            os.chmod(self.profile_dir, 0o700)

        context_options = {
            "headless": self.headless,
            "viewport": self.viewport,
            "locale": "ru-RU",
            "args": ["--lang=ru-RU"],
        }
        if self.record_video_dir:
            Path(self.record_video_dir).mkdir(parents=True, exist_ok=True)
            context_options["record_video_dir"] = self.record_video_dir
            context_options["record_video_size"] = self.viewport

        self._playwright = await async_playwright().start()
        self._context = await self._playwright.chromium.launch_persistent_context(
            self.profile_dir,
            **context_options,
        )
        self._page = self._context.pages()[0] if self._context.pages() else await self._context.new_page()
        logger.info("Browser session started, profile=%s", self.profile_dir)

    async def check_session(self, selector: str = ".chatlist, .chat-container", timeout_ms: int = 60000) -> bool:
        """Проверка, что сессия жива (элемент присутствует в DOM)."""
        if not self._page:
            return False
        try:
            await self._page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def health_check_sites(self) -> dict:
        """Проверить доступность целевых сайтов перед началом задач.

        Возвращает dict {url: bool} — доступен ли сайт.
        """
        results = {}
        if not self._context:
            return results
        for url in TARGET_SITES:
            try:
                page = await self._context.new_page()
                await page.goto(url, timeout=10000, wait_until="domcontentloaded")
                results[url] = True
                await page.close()
            except Exception as e:
                results[url] = False
                logger.warning("Site %s unavailable: %s", url, e)
        return results

    async def new_page(self, block_resources: bool = False) -> Page:
        """Создать новую страницу. При block_resources=True блокируются картинки/CSS/шрифты."""
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        if block_resources:
            await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf,ico}", lambda route: route.abort())
        return page

    async def block_non_essential_resources(self, page: Page) -> None:
        """Заблокировать несущественные ресурсы на существующей странице."""
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf,ico}", lambda route: route.abort())
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        return page

    async def close(self) -> None:
        if self._context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser session closed")

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("No active page")
        return self._page


async def capture_evidence(page: Page, task_id: str, prefix: str = "step") -> dict:
    """Снять скриншот и сохранить trace-метаинформацию для доказательства.

    Возвращает путь к артефактам. Видео настраивается на уровне контекста
    (record_video_dir) — здесь скриншот + метаданные со ссылкой на видео.
    """
    evidence_dir = Path(settings.EVIDENCE_DIR) / task_id
    evidence_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = evidence_dir / f"{prefix}.png"
    await page.screenshot(path=str(screenshot_path), full_page=True)

    video_path = None
    if page.context and page.context.options.get("record_video_dir"):
        try:
            video = await page.video()
            video_path = video.path()
        except Exception:
            video_path = None

    meta = {
        "task_id": task_id,
        "prefix": prefix,
        "screenshot": str(screenshot_path),
        "video": video_path,
        "url": page.url,
        "title": await page.title(),
    }
    meta_path = evidence_dir / f"{prefix}.meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    assert_no_secrets_in_text(json.dumps(meta), "browser.capture_evidence")
    logger.info("Evidence captured: %s", screenshot_path)
    return meta
