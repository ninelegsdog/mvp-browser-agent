from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

# Добавляем корень проекта в путь
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings
from src.security import ensure_secrets_dir, mask_secret, check_profile_permissions, assert_no_secrets_in_text
from src.queue import TaskQueue, Task
from src.proof import EvidenceRecorder, read_only_evidence


async def main():
    logging.basicConfig(level=settings.LOG_LEVEL.upper(), format="%(levelname)s %(name)s: %(message)s")

    # 1. Проверка безопасности
    ensure_secrets_dir()
    print("Secrets dir:", ensure_secrets_dir())
    print("BOT_TOKEN:", mask_secret(settings.BOT_TOKEN))
    print("DB_PASSWORD:", mask_secret(settings.DB_PASSWORD))

    # Попытка протащить секрет в лог — должна упасть
    try:
        assert_no_secrets_in_text("my key is sk-1234567890abcdef1234567890", "demo.log")
        print("ERROR: secret should have been rejected")
    except ValueError as e:
        print("OK: secret rejected:", e)

    # 2. Очередь (используем fakeredis для демонстрации без реального Redis)
    from fakeredis.aioredis import FakeRedis

    r = FakeRedis(decode_responses=True)
    q = TaskQueue(redis_url="redis://localhost")
    q._redis = r

    task = Task(task_id="demo-1", input="закажи билеты в кино", priority="high")
    await q.enqueue(task)
    popped = await q.pop()
    print("Popped task:", popped.task_id, popped.input)
    await q.save_result(popped.task_id, {"status": "done"})
    result = await q.get_result(popped.task_id)
    print("Result:", result)
    await r.aclose()

    # 3. Доказательство
    ev = EvidenceRecorder("demo-1")
    ev.record_step("start", {"url": "https://example.com"})
    ev.record_success({"status": "done"})
    info = read_only_evidence("demo-1")
    print("Evidence read-only:", info["read_only"], "files:", info["files"])

    # 4. Профиль браузера (проверка прав)
    prof_ok = check_profile_permissions(settings.BROWSER_PROFILE_DIR)
    print("Browser profile 0700:", prof_ok)

    print("\nDemo complete. Check evidence/ and .env for configuration.")


if __name__ == "__main__":
    asyncio.run(main())
