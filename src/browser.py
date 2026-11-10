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
from typing import Dict, Optional

from playwright.async_api import async_playwright, BrowserContext, Page

from src.config import settings
from src.browser_encryption import encrypt_directory_contents, decrypt_directory_contents, is_encrypted
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
        navigation_timeout_ms: int = 30000,
        action_timeout_ms: int = 10000,
    ):
        self.profile_dir = profile_dir or settings.BROWSER_PROFILE_DIR
        self.headless = headless if headless is not None else settings.BROWSER_HEADLESS
        self.viewport = {
            "width": viewport_width or settings.BROWSER_VIEWPORT_WIDTH,
            "height": viewport_height or settings.BROWSER_VIEWPORT_HEIGHT,
        }
        self.record_video_dir = record_video_dir
        self.navigation_timeout_ms = navigation_timeout_ms
        self.action_timeout_ms = action_timeout_ms
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def start(self) -> None:
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        if not check_profile_permissions(self.profile_dir):
            logger.warning("Profile dir %s is not 0700 — fixing", self.profile_dir)
            os.chmod(self.profile_dir, 0o700)

        # Расшифровываем профиль перед запуском, если он зашифрован
        if is_encrypted(self.profile_dir):
            logger.info("Профиль браузера зашифрован — расшифровываем перед запуском")
            decrypt_directory_contents(self.profile_dir)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--lang=ru-RU", "--no-sandbox", "--disable-setuid-sandbox"],
        )

        # Первичный контекст с постоянным профилем
        context_options = {
            "headless": self.headless,
            "viewport": self.viewport,
            "locale": "ru-RU",
        }
        if self.record_video_dir:
            Path(self.record_video_dir).mkdir(parents=True, exist_ok=True)
            context_options["record_video_dir"] = self.record_video_dir
            context_options["record_video_size"] = self.viewport

        self._context = await self._browser.new_context(**context_options)
        # Применяем постоянный профиль через хранилище состояния
        if self.profile_dir and Path(self.profile_dir).exists():
            await self._context.add_init_script(
                f"localStorage.clear(); sessionStorage.clear();"
            )
        self._page = self._context.pages()[0] if self._context.pages() else await self._context.new_page()
        self._contexts: Dict[str, BrowserContext] = {"default": self._context}
        logger.info("Browser session started, profile=%s, contexts=%d", self.profile_dir, len(self._contexts))

    async def check_session(self, selector: str = ".chatlist, .chat-container", timeout_ms: int = 60000) -> bool:
        """Проверка, что сессия жива (элемент присутствует в DOM)."""
        if not self._page:
            return False
        try:
            await self._page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> Page:
        """Перейти по URL с таймаутом навигации."""
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        try:
            await page.goto(url, timeout=self.navigation_timeout_ms, wait_until=wait_until)
        except Exception as e:
            await page.close()
            raise e
        return page

    async def health_check_sites(self) -> dict:
        """Проверить доступность целевых сайтов перед началом задач.

        Возвращает dict {url: bool} — доступен ли сайт.
        """
        results = {}
        if not self._context:
            return results
        for url in TARGET_SITES:
            try:
                page = await self.navigate(url, wait_until="domcontentloaded")
                results[url] = True
                await page.close()
            except Exception as e:
                results[url] = False
                logger.warning("Site %s unavailable: %s", url, e)
        return results

    async def new_page(self, block_resources: bool = False, auto_dismiss_cookies: bool = True, handle_dialogs: bool = True) -> Page:
        """Создать новую страницу.

        При block_resources=True блокируются картинки/CSS/шрифты.
        При auto_dismiss_cookies=True автоматически отменяет баннеры куки.
        При handle_dialogs=True автоматически принимает диалоги (alert/confirm/prompt).
        """
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        if block_resources:
            await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf,ico}", lambda route: route.abort())
        if auto_dismiss_cookies:
            await self._setup_cookie_banner_handler(page)
        if handle_dialogs:
            await self._setup_dialog_handler(page)
        return page

    async def block_non_essential_resources(self, page: Page) -> None:
        """Заблокировать несущественные ресурсы на существующей странице."""
        await page.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2,ttf,ico}", lambda route: route.abort())
        if not self._context:
            raise RuntimeError("Browser not started")
        page = await self._context.new_page()
        return page

    async def _setup_cookie_banner_handler(self, page: Page) -> None:
        """Автоматически отклонять баннеры куки на известных селекторах."""
        cookie_selectors = [
            'button:has-text("Отклонить")',
            'button:has-text("Reject")',
            'button:has-text("Отказаться")',
            'button:has-text("Decline")',
            'button:has-text("Only essential")',
            'button:has-text("Только необходимые")',
            '[data-testid="cookie-reject"]',
            '[aria-label="Reject cookies"]',
            '[aria-label="Отклонить куки"]',
        ]

        async def handle_cookie_banner():
            for selector in cookie_selectors:
                try:
                    element = page.locator(selector).first
                    if await element.is_visible(timeout=2000):
                        await element.click(timeout=2000)
                        logger.info("Отклонён баннер куки: %s", selector)
                except Exception:
                    pass

        # Проверяем при загрузке страницы
        page.on("load", lambda _: asyncio.create_task(handle_cookie_banner()))

    async def _setup_dialog_handler(self, page: Page) -> None:
        """Автоматически принимать или отклонять диалоги (alert, confirm, prompt)."""
        async def handle_dialog(dialog):
            dialog_type = dialog.type
            message = dialog.message[:100] if dialog.message else ""
            logger.warning("Диалог %s: %s", dialog_type, message)
            # По умолчанию принимаем диалоги (подтверждаем действие)
            await dialog.accept()

        page.on("dialog", handle_dialog)

    async def create_isolated_context(self, context_id: str = "isolated") -> BrowserContext:
        """Создать изолированный контекст браузера (отдельные куки, хранилище, сессия).

        Полезно для работы с разными аккаунтами одновременно.
        """
        if not self._browser:
            raise RuntimeError("Browser not started")
        if context_id in self._contexts:
            raise ValueError(f"Context {context_id} already exists")
        context = await self._browser.new_context(
            headless=self.headless,
            viewport=self.viewport,
            locale="ru-RU",
        )
        self._contexts[context_id] = context
        logger.info("Создан изолированный контекст %s (всего %d)", context_id, len(self._contexts))
        return context

    async def get_context(self, context_id: str = "default") -> BrowserContext:
        """Получить контекст по ID."""
        if context_id not in self._contexts:
            raise ValueError(f"Context {context_id} not found")
        return self._contexts[context_id]

    async def close_context(self, context_id: str) -> None:
        """Закрыть конкретный контекст."""
        if context_id not in self._contexts:
            logger.warning("Context %s not found for closing", context_id)
            return
        context = self._contexts[context_id]
        await context.close()
        del self._contexts[context_id]
        logger.info("Контекст %s закрыт (осталось %d)", context_id, len(self._contexts))

    async def close(self) -> None:
        # Закрываем все контексты
        for context_id in list(self._contexts.keys()):
            await self.close_context(context_id)
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        logger.info("Browser session closed")

        # Шифруем профиль после остановки, чтобы защитить сессию на диске
        if self.profile_dir and Path(self.profile_dir).exists():
            encrypted_count = encrypt_directory_contents(self.profile_dir)
            logger.info("Профиль браузера зашифрован (%d файлов)", encrypted_count)

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
