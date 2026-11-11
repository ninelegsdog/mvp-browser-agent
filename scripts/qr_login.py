"""Ручной вход по QR в постоянный профиль браузера.

Клиент один раз входит с телефона — дальше агент работает в этой же сессии.
Скрипт ничего не спрашивает с клавиатуры и не работает с секретами: он
открывает страницу входа, периодически сохраняет скриншот (его видно в
Telegram) и честно сообщает, удалось ли подтвердить вход.

Примеры:

    # Ozon: признак входа — появился личный кабинет
    python scripts/qr_login.py --url https://www.ozon.ru --success-selector "[data-widget=profile]"

    # Avito
    python scripts/qr_login.py --url https://www.avito.ru --success-selector "[data-marker=header-profile]"

    # Без критерия: только скриншоты, вход останется неподтверждённым
    python scripts/qr_login.py --url https://www.ozon.ru

Коды возврата: 0 — вход подтверждён, 1 — не дождались, 2 — подтвердить нечем.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVIDENCE_DIR = ROOT / "evidence" / "qr-login"

logger = logging.getLogger(__name__)


def _slug(url: str) -> str:
    """Короткое безопасное имя файла из URL."""
    cleaned = url.replace("https://", "").replace("http://", "").strip("/")
    return "".join(char if char.isalnum() or char in "-._" else "_" for char in cleaned)[:60] or "login"


def screenshot_name(url: str, index: int) -> str:
    """Имя скриншота: сайт + номер кадра, чтобы порядок был виден."""
    return f"{_slug(url)}-{index:03d}.png"


async def _is_confirmed(
    page,
    success_selector: str | None,
    success_url_contains: str | None,
    wait_seconds: int,
) -> bool:
    """Проверить признак входа: селектор или подстрока URL."""
    if success_selector:
        try:
            await page.wait_for_selector(success_selector, timeout=wait_seconds * 1000)
        except Exception as error:  # noqa: BLE001 - элемент ещё не появился, это ожидаемо
            logger.debug("Признак входа не появился: %s", error)
            return False
        return True
    if success_url_contains:
        return success_url_contains in page.url
    return False


async def wait_for_login(
    url: str,
    success_selector: str | None,
    success_url_contains: str | None,
    timeout_seconds: int,
    interval_seconds: int,
    evidence_dir: Path,
    headless: bool,
) -> int:
    """Открыть страницу и ждать подтверждения входа. Возвращает код выхода."""
    try:
        from src.browser import BrowserSession
    except (ImportError, ValueError) as error:
        print(f"Не удалось загрузить настройки: {error}")
        print("Сначала заполните .env и выполните: python scripts/preflight.py")
        return 2

    evidence_dir.mkdir(parents=True, exist_ok=True)
    session = BrowserSession(headless=headless)

    print(f"Открываю {url}")
    await session.start()
    page = await session.navigate(url)
    print(f"Скриншоты: {evidence_dir}")
    print("Войдите с телефона по QR. Скрипт закроется сам по таймауту.")

    has_criterion = bool(success_selector or success_url_contains)
    if not has_criterion:
        print("Критерий входа не задан: подтвердить вход будет нечем (код 2).")
        print("Укажите --success-selector или --success-url-contains.")

    confirmed = False
    frames = 0
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            frames += 1
            path = evidence_dir / screenshot_name(url, frames)
            await page.screenshot(path=str(path), full_page=False)
            stamp = datetime.now(UTC).strftime("%H:%M:%S")
            print(f"[{stamp}] кадр {frames}: {path.name}")

            if await _is_confirmed(page, success_selector, success_url_contains, interval_seconds):
                confirmed = True
                break
            await asyncio.sleep(interval_seconds)
    finally:
        await session.close()

    if confirmed:
        print("ИТОГ: вход подтверждён. Профиль сохранён, сессия переживёт перезапуск.")
        return 0
    if not has_criterion:
        print("ИТОГ: вход не подтверждён — не был задан критерий проверки (код 2).")
        print(f"Скриншоты: {evidence_dir}. Проверьте глазами и задайте критерий.")
        return 2
    print("ИТОГ: вход не подтверждён за отведённое время (код 1).")
    print(f"Скриншоты: {evidence_dir}. Проверьте: QR не считался или сайт просит капчу.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ручной вход по QR в постоянный профиль")
    parser.add_argument("--url", required=True, help="страница входа, например https://www.ozon.ru")
    parser.add_argument("--success-selector", default=None, help="CSS-селектор, который есть только после входа")
    parser.add_argument("--success-url-contains", default=None, help="подстрока URL, которая появляется после входа")
    parser.add_argument("--timeout", type=int, default=600, help="сколько секунд ждать вход")
    parser.add_argument("--interval", type=int, default=15, help="как часто сохранять скриншот")
    parser.add_argument("--evidence-dir", default=str(DEFAULT_EVIDENCE_DIR))
    parser.add_argument("--show", action="store_true", help="показать окно браузера (нужен дисплей)")
    args = parser.parse_args(argv)

    if args.timeout < args.interval:
        print("Таймаут меньше интервала: не будет ни одного кадра.")
        return 2

    return asyncio.run(
        wait_for_login(
            url=args.url,
            success_selector=args.success_selector,
            success_url_contains=args.success_url_contains,
            timeout_seconds=args.timeout,
            interval_seconds=args.interval,
            evidence_dir=Path(args.evidence_dir),
            headless=not args.show,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
