"""Браузер с сохранённой сессией: Playwright + постоянный профиль Chromium.

- launch_persistent_context с профилем на диске (сессия живёт между запусками).
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
from typing import Literal

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from src.browser_encryption import decrypt_profile, encrypt_profile, is_profile_encrypted
from src.config import settings
from src.security import assert_no_secrets_in_text, check_profile_permissions

TARGET_SITES = ["https://www.ozon.ru", "https://www.avito.ru", "https://www.wildberries.ru"]

logger = logging.getLogger(__name__)


class BrowserSession:
    """Один постоянный профиль Chromium с живой сессией."""

    def __init__(
        self,
        profile_dir: str | None = None,
        headless: bool | None = None,
        viewport_width: int | None = None,
        viewport_height: int | None = None,
        record_video_dir: str | None = None,
        proxy: str | None = None,
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
        self.proxy = proxy or settings.BROWSER_PROXY
        self.navigation_timeout_ms = navigation_timeout_ms
        self.action_timeout_ms = action_timeout_ms
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._isolated_dirs: dict[str, str] = {}

    def _context_options(self) -> dict:
        """Параметры запуска постоянного контекста (общие для всех профилей)."""
        options: dict = {
            "headless": self.headless,
            "args": ["--lang=ru-RU", "--no-sandbox", "--disable-setuid-sandbox"],
            "viewport": self.viewport,
            "locale": "ru-RU",
        }
        if self.proxy:
            options["proxy"] = {"server": self.proxy}
        if self.record_video_dir:
            Path(self.record_video_dir).mkdir(parents=True, exist_ok=True)
            options["record_video_dir"] = self.record_video_dir
            options["record_video_size"] = self.viewport
        return options

    async def start(self) -> None:
        Path(self.profile_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        if not check_profile_permissions(self.profile_dir):
            logger.warning("Profile dir %s is not 0700 — fixing", self.profile_dir)
            os.chmod(self.profile_dir, 0o700)

        await self._decrypt_profile_if_needed(self.profile_dir)

        self._playwright = await async_playwright().start()

        # Постоянный профиль: куки и сессия (включая вход по QR с телефона)
        # переживают перезапуск процесса. Именно этот профиль пользователь
        # логинит вручную через телефон на своём VPS.
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            **self._context_options(),
        )
        pages = self._context.pages
        self._page = pages[0] if pages else await self._context.new_page()
        self._contexts = {"default": self._context}
        if self._page:
            await self._setup_dialog_handler(self._page)
        logger.info(
            "Браузер запущен с постоянным профилем %s (контекстов: %d)",
            self.profile_dir,
            len(self._contexts),
        )

    async def check_session(self, selector: str = ".chatlist, .chat-container", timeout_ms: int = 60000) -> bool:
        """Проверка, что сессия жива (элемент присутствует в DOM)."""
        if not self._page:
            return False
        try:
            await self._page.wait_for_selector(selector, timeout=timeout_ms)
            return True
        except Exception:
            return False

    async def navigate(
        self, url: str, wait_until: Literal["commit", "domcontentloaded", "load", "networkidle"] = "domcontentloaded"
    ) -> Page:
        """Перейти по URL с таймаутом навигации."""
        if not self._context:
            raise RuntimeError("Browser not started")

        # Переходим в текущей странице, а не создаём новую на каждый шаг:
        # иначе каждая навигация оставляет открытую вкладку, а check_session
        # смотрит на старую страницу и отвечает про неё.
        page = self._page if self._page is not None and not self._page.is_closed() else None
        if page is None:
            page = await self._context.new_page()
            self._page = page
        try:
            await page.goto(url, timeout=self.navigation_timeout_ms, wait_until=wait_until)
        except Exception:
            await page.close()
            if self._page is page:
                self._page = None
            raise
        return page

    async def health_check_sites(self) -> dict:
        """Проверить доступность целевых сайтов перед началом задач.

        Возвращает dict {url: bool} — доступен ли сайт.
        """
        results: dict[str, bool] = {}
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

    async def new_page(
        self,
        block_resources: bool = False,
        auto_dismiss_cookies: bool = True,
        handle_dialogs: bool = True,
    ) -> Page:
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
                except Exception as e:
                    logger.debug("Баннер куки не обработан (%s): %s", selector, e)

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

        В режиме постоянного профиля отдельный контекст — это отдельная
        директория профиля: у каждого аккаунта своя сессия, которая тоже
        переживает перезапуск.
        """
        if not self._playwright:
            raise RuntimeError("Browser not started")
        if context_id in self._contexts:
            raise ValueError(f"Context {context_id} already exists")

        profile_path = Path(f"{self.profile_dir}-{context_id}")
        profile_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(profile_path, 0o700)
        await self._decrypt_profile_if_needed(str(profile_path))

        context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            **self._context_options(),
        )
        self._contexts[context_id] = context
        self._isolated_dirs[context_id] = str(profile_path)
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
        isolated = self._isolated_dirs.pop(context_id, None)
        if isolated:
            await self._encrypt_profile_if_enabled(isolated)
        logger.info("Контекст %s закрыт (осталось %d)", context_id, len(self._contexts))

    async def close(self) -> None:
        # Закрываем все контексты (в persistent-режиме они и есть браузеры)
        for context_id in list(self._contexts.keys()):
            await self.close_context(context_id)
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser session closed")

        # Шифруем профиль после остановки, чтобы защитить сессию на диске.
        # По умолчанию выключено: основная защита — права 0700 и профиль вне git.
        await self._encrypt_profile_if_enabled(self.profile_dir)

    async def _decrypt_profile_if_needed(self, directory: str) -> None:
        """Расшифровать профиль до запуска Chromium (в отдельном потоке)."""
        self._check_profile_encryption(directory)
        if not is_profile_encrypted(directory):
            return
        logger.info("Профиль %s зашифрован — расшифровываем перед запуском", directory)
        count = await asyncio.to_thread(decrypt_profile, directory)
        logger.info("Профиль %s расшифрован (%d файлов)", directory, count)

    async def _encrypt_profile_if_enabled(self, directory: str) -> None:
        """Зашифровать профиль после остановки Chromium, если включено."""
        self._check_profile_encryption(directory)
        if not settings.BROWSER_PROFILE_ENCRYPTION:
            return
        if not Path(directory).is_dir():
            return
        count = await asyncio.to_thread(encrypt_profile, directory)
        if count:
            logger.info("Профиль %s зашифрован (%d файлов)", directory, count)
        else:
            logger.info("Профиль %s уже был зашифрован", directory)

    @staticmethod
    def _check_profile_encryption(directory: str) -> None:
        """Предупредить, если профиль зашифрован, а шифрование выключено.

        Такой профиль расшифровывается всегда: иначе Chromium не сможет с ним
        работать. Предупреждение нужно, чтобы выключенный флаг не выглядел как
        «шифрование работает».
        """
        if not settings.BROWSER_PROFILE_ENCRYPTION and is_profile_encrypted(directory):
            logger.warning(
                "Профиль %s зашифрован, хотя BROWSER_PROFILE_ENCRYPTION выключен. "
                "Расшифровываю для запуска; включите флаг, если шифрование нужно.",
                directory,
            )

    @property
    def is_started(self) -> bool:
        """Браузер запущен и готов принимать команды."""
        return self._context is not None

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
    video = page.video
    if video is not None:
        try:
            video_path = await video.path()
        except Exception as e:
            logger.warning("Не удалось получить путь к видео: %s", e)

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
