"""Демонстрационный прогон всех компонентов MVP.

Запускает агентную среду с автономным браузерным агентом,
очередью задач, доказательствами результата и безопасностью.
"""

import asyncio
import logging
import os
import sys

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings
from src.security import (
    ensure_secrets_dir,
    mask_secret,
    check_profile_permissions,
    assert_no_secrets_in_text,
)
from src.queue import TaskQueue, Task
from src.proof import EvidenceRecorder, read_only_evidence
from src.agent import Agent, AgentTask
from src.queue_interface import TaskQueueInterface
from src.browser import BrowserSession
from src.browser_tool import BrowserTool
from src.autonomous_planner import AutonomousPlanner, LLMClient
from src.tools import Tool, shell_tool, http_tool, memory_tool


async def main():
    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(levelname)s %(name)s: %(message)s",
    )

    # 1. Проверка безопасности
    ensure_secrets_dir()
    print("Секретное хранилище:", ensure_secrets_dir())
    print("BOT_TOKEN:", mask_secret(settings.BOT_TOKEN))
    print("DB_PASSWORD:", mask_secret(settings.DB_PASSWORD))
    print("LLM_API_KEY:", mask_secret(settings.LLM_API_KEY))

    # Попытка протащить секрет в лог — должна упасть
    try:
        assert_no_secrets_in_text("my key is sk-1234567890abcdef1234567890", "demo.log")
        print("ОШИБКА: секрет должен быть отклонён")
    except ValueError as e:
        print("OK: секрет отвергнут:", e)

    # 2. Инициализация браузера
    browser_session = BrowserSession()
    print(f"Браузер инициализирован: профиль {browser_session.profile_dir}")

    # 3. Инициализация агентных инструментов
    browser_tool = BrowserTool(browser_session)
    tools = {
        "shell": Tool("shell", shell_tool, "Выполнить команду shell"),
        "http": Tool("http", http_tool, "HTTP-запрос к внешнему сервису"),
        "browser": browser_tool,  # Реальный Playwright браузер
        "memory": Tool("memory", memory_tool, "Память задачи и предпочтений"),
    }

    # 4. Инициализация LLM-планировщика
    llm_client = LLMClient(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        model=settings.LLM_MODEL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
    )
    planner = AutonomousPlanner(llm_client)

    # 5. Инициализация агента
    agent_instance = Agent(tools=tools, planner=planner, browser_session=browser_session)

    # 6. Демонстрация очереди задач (используем fakeredis для демонстрации)
    from fakeredis.aioredis import FakeRedis

    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task = Task(task_id="demo-1", input_text="закажи билеты в кино", priority="high")
    await q.enqueue(task)
    popped = await q.pop()
    print("Извлечена задача:", popped.task_id, popped.input)
    await q.save_result(popped.task_id, {"status": "done"})
    result = await q.get_result(popped.task_id)
    print("Результат:", result)
    await r.aclose()

    # 7. Демонстрация доказательств
    ev = EvidenceRecorder("demo-1")
    ev.record_step("start", {"url": "https://example.com"})
    ev.record_success({"status": "done"})
    info = read_only_evidence("demo-1")
    print("Доказательства доступны на чтение:", info["read_only"], "файлы:", info["files"])

    # 8. Проверка профиля браузера
    prof_ok = check_profile_permissions(settings.BROWSER_PROFILE_DIR)
    print("Профиль браузера 0700:", prof_ok)

    print("\nДемо завершено. Проверьте evidence/ и .env для конфигурации.")
    print("\nДля запуска в реальном режиме:")
    print("  python -m src.telegram_bot   # Telegram-бот")
    print("  python -m src.queue worker   # Очередь задач")


async def main_wrapper():
    try:
        await main()
    finally:
        # Закрываем LLM клиент если он есть
        pass


if __name__ == "__main__":
    asyncio.run(main_wrapper())