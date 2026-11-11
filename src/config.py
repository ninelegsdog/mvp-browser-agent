import logging
import sys
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


# Проверка версии Python при старте
MIN_PYTHON_VERSION = (3, 12)
if sys.version_info < MIN_PYTHON_VERSION:
    print(
        f"Ошибка: требуется Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+, "
        f"установлен {sys.version_info[0]}.{sys.version_info[1]}",
        file=sys.stderr,
    )
    sys.exit(1)


class Settings(BaseSettings):
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_PASSWORD: str = ""

    QUEUE_HIGH: str = "queue:high"
    QUEUE_MED: str = "queue:med"
    QUEUE_LOW: str = "queue:low"
    DLQ: str = "queue:dead"

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "mvp_agent"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""

    BOT_TOKEN: str = ""
    BOT_ADMIN_ID: int = 0

    # LLM API (OpenAI-совместимый)
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o"
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 2000

    BROWSER_PROFILE_DIR: str = "./browser-profile"
    BROWSER_HEADLESS: bool = False
    BROWSER_VIEWPORT_WIDTH: int = 1280
    BROWSER_VIEWPORT_HEIGHT: int = 800
    # Прокси для браузера (если маркетплейс недоступен с VPS напрямую):
    # http://user:pass@host:port или socks5://host:port. Пусто — без прокси.
    BROWSER_PROXY: str = ""

    EVIDENCE_DIR: str = "./evidence"

    LOG_LEVEL: str = "INFO"
    METRICS_PORT: int = 9090
    # В контейнере слушаем все интерфейсы; снаружи порт публикуется явно.
    METRICS_HOST: str = "0.0.0.0"  # noqa: S104 — слушаем внутри контейнера, порт публикуется явно

    MAX_HOURS_PER_CLIENT_PER_MONTH: int = 60

    # Сколько секунд ждать ручной вход по QR, прежде чем сказать «не вошли»
    QR_LOGIN_TIMEOUT_SECONDS: int = 600

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @model_validator(mode="after")
    def validate_required_settings(self) -> Self:
        """Валидация критичных настроек — приложение не стартует без них."""
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN обязателен для запуска")
        if self.BOT_ADMIN_ID == 0:
            raise ValueError("BOT_ADMIN_ID обязателен и не должен быть 0")
        if not self.DB_PASSWORD:
            raise ValueError("DB_PASSWORD обязателен для запуска")
        if not self.REDIS_PASSWORD:
            raise ValueError("REDIS_PASSWORD обязателен для запуска")
        if not self.LLM_API_KEY:
            logger.warning(
                "LLM_API_KEY не задан. Автономный агент не будет работать. "
                "Установите LLM_API_KEY в .env для включения автономного режима."
            )
        return self


settings = Settings()

# Приоритеты в порядке обслуживания BLPOP (первый непустой выигрывает)
PRIORITY_QUEUES: list[str] = [settings.QUEUE_HIGH, settings.QUEUE_MED, settings.QUEUE_LOW]
