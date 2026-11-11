"""Предстартовая проверка: что уже готово, а каких данных не хватает.

Запускается ДО первого старта бота и не требует валидного `.env`:
читает файл как текст и перечисляет незаполненные пункты по именам.
Так видно ровно то, что нужно добавить, без чтения документации.

    python scripts/preflight.py              # только конфигурация, без сети
    python scripts/preflight.py --network    # плюс Redis, PostgreSQL и сайты

Коды возврата: 0 — можно запускать, 1 — не хватает данных, 2 — проверить нечем.
"""

from __future__ import annotations

import argparse
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

# Значения из .env.example — это не данные клиента, а подсказки.
PLACEHOLDER_VALUES = {
    "",
    "change-me",
    "change-me-strong-redis-password",
    "change-me-strong-db-password",
    "123456789:ABCdefGHIjklMNOpqrsTUVwxyz",
    "123456789",
    "0",
}

REQUIRED: dict[str, str] = {
    "BOT_TOKEN": "токен бота от @BotFather",
    "BOT_ADMIN_ID": "ваш Telegram ID (узнать у @userinfobot)",
    "DB_PASSWORD": "пароль PostgreSQL",
    "REDIS_PASSWORD": "пароль Redis",
}

RECOMMENDED: dict[str, str] = {
    "LLM_API_KEY": "ключ LLM-провайдера — без него агент не принимает решений",
}

# Эти значения имеют рабочий дефолт в src/config.py: отсутствие в .env — не ошибка.
DEFAULTS: dict[str, str] = {
    "LLM_BASE_URL": "https://api.openai.com/v1",
    "LLM_MODEL": "gpt-4o",
}

TOKEN_PATTERN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")
TARGET_SITES = ("https://www.ozon.ru", "https://www.avito.ru", "https://www.wildberries.ru")


@dataclass
class Check:
    """Одна проверка: имя, статус и что делать."""

    name: str
    ok: bool
    detail: str

    @property
    def mark(self) -> str:
        return "OK  " if self.ok else "НЕТ "


def read_env(path: Path) -> dict[str, str]:
    """Прочитать .env без pydantic: файл может быть ещё не заполнен."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def is_filled(value: str | None) -> bool:
    """Значение заполнено, если оно есть и не совпадает с подсказкой из примера."""
    if value is None:
        return False
    return value.strip() not in PLACEHOLDER_VALUES


def check_env_file(env_path: Path) -> Check:
    if env_path.exists():
        return Check("Файл .env", True, str(env_path))
    return Check(
        "Файл .env",
        False,
        f"нет файла {env_path}. Скопируйте: cp .env.example .env",
    )


def check_required(env: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for key, purpose in REQUIRED.items():
        value = env.get(key)
        if not is_filled(value):
            checks.append(Check(key, False, f"заполните: {purpose}"))
            continue
        if key == "BOT_TOKEN" and not TOKEN_PATTERN.match(value or ""):
            checks.append(Check(key, False, "формат токена: 123456789:AA... (перевыпустите у @BotFather)"))
            continue
        if key == "BOT_ADMIN_ID" and not (value or "").isdigit():
            checks.append(Check(key, False, "нужно число, а не имя пользователя"))
            continue
        checks.append(Check(key, True, "заполнено"))
    return checks


def check_recommended(env: dict[str, str]) -> list[Check]:
    checks: list[Check] = []
    for key, purpose in RECOMMENDED.items():
        filled = is_filled(env.get(key))
        detail = "заполнено" if filled else f"не задано: {purpose}"
        checks.append(Check(key, filled, detail))
    for key, default in DEFAULTS.items():
        value = env.get(key)
        detail = f"из .env: {value}" if is_filled(value) else f"по умолчанию: {default}"
        checks.append(Check(key, True, detail))
    return checks


def check_profile(env: dict[str, str]) -> Check:
    profile = Path(env.get("BROWSER_PROFILE_DIR") or "./browser-profile")
    if not profile.is_absolute():
        profile = ROOT / profile
    if not profile.exists():
        # Каталога ещё нет — это нормальное состояние до первого запуска.
        return Check("Профиль браузера", True, f"{profile} создастся при первом запуске")
    mode = stat.S_IMODE(profile.stat().st_mode)
    if mode != 0o700:
        return Check("Профиль браузера", False, f"права {oct(mode)}, нужно 0700: chmod 700 {profile}")
    return Check("Профиль браузера", True, f"{profile} (0700)")


def check_playwright() -> Check:
    """Проверить, что Chromium для Playwright скачан, а не только пакет установлен."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return Check("Chromium", False, "нет пакета playwright: pip install -r requirements.txt")

    try:
        with sync_playwright() as p:
            executable = p.chromium.executable_path
    except Exception as error:  # noqa: BLE001 - сообщаем причину как есть
        return Check("Chromium", False, f"не удалось проверить: {error}")

    if executable and Path(executable).exists():
        return Check("Chromium", True, executable)
    return Check("Chromium", False, "браузер не скачан: playwright install chromium")


def check_evidence_dir(env: dict[str, str]) -> Check:
    evidence = Path(env.get("EVIDENCE_DIR") or "./evidence")
    if not evidence.is_absolute():
        evidence = ROOT / evidence
    try:
        evidence.mkdir(parents=True, exist_ok=True)
        probe = evidence / ".preflight-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as error:
        return Check("Каталог доказательств", False, f"{evidence}: {error}")
    return Check("Каталог доказательств", True, str(evidence))


def check_redis(env: dict[str, str]) -> Check:
    try:
        import redis
    except ImportError:
        return Check("Redis", False, "нет пакета redis: pip install -r requirements.txt")

    url = env.get("REDIS_URL") or "redis://localhost:6379"
    password = env.get("REDIS_PASSWORD") or ""
    try:
        client = redis.Redis.from_url(url, password=password or None, socket_connect_timeout=5)
        client.ping()
        client.close()
    except Exception as error:  # noqa: BLE001 - сеть отвечает разными типами ошибок
        return Check("Redis", False, f"{url}: {error}")
    return Check("Redis", True, url)


def check_postgres(env: dict[str, str]) -> Check:
    try:
        import asyncio

        import asyncpg
    except ImportError:
        return Check("PostgreSQL", False, "нет пакета asyncpg: pip install -r requirements.txt")

    async def probe() -> None:
        connection = await asyncpg.connect(
            host=env.get("DB_HOST") or "localhost",
            port=int(env.get("DB_PORT") or 5432),
            database=env.get("DB_NAME") or "mvp_agent",
            user=env.get("DB_USER") or "postgres",
            password=env.get("DB_PASSWORD") or "",
            timeout=5,
        )
        try:
            await connection.fetch("SELECT 1")
        finally:
            await connection.close()

    try:
        asyncio.run(probe())
    except Exception as error:  # noqa: BLE001
        return Check("PostgreSQL", False, str(error))
    return Check("PostgreSQL", True, "соединение установлено")


def check_sites() -> list[Check]:
    try:
        import httpx
    except ImportError:
        return [Check("Целевые сайты", False, "нет пакета httpx")]

    checks: list[Check] = []
    for url in TARGET_SITES:
        try:
            response = httpx.get(url, timeout=10, follow_redirects=True)
        except Exception as error:  # noqa: BLE001
            checks.append(Check(url, False, str(error)))
            continue
        checks.append(Check(url, response.status_code < 500, f"HTTP {response.status_code}"))
    return checks


def run(env_path: Path = ENV_PATH, network: bool = False) -> int:
    """Выполнить проверки и напечатать отчёт. Возвращает код выхода."""
    env = read_env(env_path)

    checks: list[Check] = [check_env_file(env_path)]
    checks.extend(check_required(env))
    checks.extend(check_recommended(env))
    checks.append(check_profile(env))
    checks.append(check_playwright())
    checks.append(check_evidence_dir(env))
    if network:
        checks.append(check_redis(env))
        checks.append(check_postgres(env))
        checks.extend(check_sites())

    print("ПРЕДСТАРТОВАЯ ПРОВЕРКА")
    print("=" * 60)
    for check in checks:
        print(f"[{check.mark}] {check.name}: {check.detail}")

    required_names = set(REQUIRED) | {"Файл .env", "Chromium", "Каталог доказательств", "Профиль браузера"}
    required_failed = [c for c in checks if not c.ok and c.name in required_names]
    recommended_failed = [c for c in checks if not c.ok and c.name in RECOMMENDED]

    print("=" * 60)
    if recommended_failed:
        print("ВНИМАНИЕ: без этих пунктов агент не будет принимать решения сам:")
        for check in recommended_failed:
            print(f"  - {check.name}: {check.detail}")

    if required_failed:
        print(f"ИТОГ: не хватает обязательных пунктов: {len(required_failed)} — запускать рано.")
        return 1

    print("ИТОГ: обязательные данные на месте. Можно запускать.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверка готовности к запуску")
    parser.add_argument("--network", action="store_true", help="проверить Redis, PostgreSQL и целевые сайты")
    parser.add_argument("--env-file", default=str(ENV_PATH), help="путь к .env")
    args = parser.parse_args(argv)
    return run(Path(args.env_file), network=args.network)


if __name__ == "__main__":
    sys.exit(main())
