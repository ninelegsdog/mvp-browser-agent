"""Ежедневный отчёт о состоянии системы MVP.

Генерирует отчёт с метриками, ошибками и рекомендациями.
Может запускаться по cron или вручную.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import settings
from src.metrics import get_memory_usage, get_success_rate


async def get_queue_stats():
    """Получить статистику очереди."""
    from src.queue import TaskQueue

    q = TaskQueue()
    await q.connect()

    stats = {}
    for qname in [settings.QUEUE_HIGH, settings.QUEUE_MED, settings.QUEUE_LOW, settings.DLQ]:
        llen = await q._redis.llen(qname)
        stats[qname] = llen

    await q.aclose()
    return stats


async def generate_daily_report():
    """Генерация ежедневного отчёта о состоянии системы."""
    report = {
        "timestamp": datetime.now(UTC).isoformat(),
        "system": "MVP Browser Agent",
        "version": "0.1.0",
    }

    # Очередь
    queue_stats = await get_queue_stats()
    report["queue"] = queue_stats

    # SLO/SLI
    report["slo"] = {
        "uptime_target": "99.9%",
        "latency_p95_target": "2 сек",
        "error_rate_target": "1%",
    }

    # Успешность задач
    success_rate = get_success_rate()
    if success_rate is not None:
        report["success_rate"] = success_rate
        report["success_rate_percent"] = f"{success_rate:.2%}"

    # Память
    memory = get_memory_usage()
    if memory:
        report["memory"] = memory

    # Оценка состояния
    issues = []
    if success_rate is not None and success_rate < 0.90:
        issues.append(f"Successful rate ниже порога: {success_rate:.2%}")
    if queue_stats.get(settings.DLQ, 0) > 10:
        issues.append(f"Много задач в DLQ: {queue_stats.get(settings.DLQ, 0)}")

    report["issues"] = issues
    report["status"] = "healthy" if not issues else "degraded"

    # Вывод отчёта
    print(json.dumps(report, indent=2, ensure_ascii=False))

    # Сохранение в файл
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / f"daily_report_{datetime.now().strftime('%Y%m%d')}.json"
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nОтчёт сохранён: {report_file}")

    return report


if __name__ == "__main__":
    asyncio.run(generate_daily_report())
