from pydantic_settings import BaseSettings
from typing import List


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

    BROWSER_PROFILE_DIR: str = "./browser-profile"
    BROWSER_HEADLESS: bool = True
    BROWSER_VIEWPORT_WIDTH: int = 1280
    BROWSER_VIEWPORT_HEIGHT: int = 800

    EVIDENCE_DIR: str = "./evidence"

    LOG_LEVEL: str = "INFO"
    METRICS_PORT: int = 9090

    MAX_HOURS_PER_CLIENT_PER_MONTH: int = 60

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


settings = Settings()

# Приоритеты в порядке обслуживания BLPOP (первый непустой выигрывает)
PRIORITY_QUEUES: List[str] = [settings.QUEUE_HIGH, settings.QUEUE_MED, settings.QUEUE_LOW]
