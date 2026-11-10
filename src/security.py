"""Безопасность: секреты не в коде, не в чате, не в git.

Паттерн: .env -> .env.age, ключи в ~/.secrets/keys/ (chmod 600).
Никогда не логируем секреты. Профили браузера 0700, вне git.
"""

import os
import stat
from pathlib import Path
from typing import Optional


SECRETS_DIR = Path.home() / ".secrets" / "keys"
ENV_AGE = Path(".env.age")


def ensure_secrets_dir() -> Path:
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    SECRETS_DIR.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)  # 0700
    return SECRETS_DIR


def get_secret(name: str) -> Optional[str]:
    """Читает секрет из переменной окружения или файла в ~/.secrets/keys/.

    Никогда не возвращает значение в логах. Только наличие/отсутствие.
    """
    val = os.environ.get(name)
    if val:
        return val
    f = SECRETS_DIR / name
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    return None


def mask_secret(value: Optional[str]) -> str:
    """Для логов: показываем только наличие, не значение."""
    if not value:
        return "unset"
    return f"set ({len(value)} chars)"


def check_profile_permissions(profile_dir: str) -> bool:
    """Профиль браузера должен быть 0700 и не в git."""
    p = Path(profile_dir)
    if not p.exists():
        return False
    mode = p.stat().st_mode & 0o777
    return mode == 0o700


def assert_no_secrets_in_text(text: str, context: str = "") -> None:
    """Проверяет, что в тексте нет явных секретов (топ-паттерны).

    Вызывается перед логированием/коммитом._FAIL LOUD_: кидает, если найдено.
    """
    import re
    patterns = [
        r"sk-[A-Za-z0-9]{20,}",        # OpenAI
        r"ghp_[A-Za-z0-9]{36,}",        # GitHub PAT
        r"AKIA[0-9A-Z]{16}",            # AWS
        r"xoxb-[A-Za-z0-9-]{20,}",      # Slack bot
        r"\d{3}-\d{2}-\d{4}",           # SSO-like
    ]
    for pat in patterns:
        if re.search(pat, text):
            raise ValueError(
                f"Possible secret detected in {context or 'text'}. "
                "Remove before logging/commit."
            )
