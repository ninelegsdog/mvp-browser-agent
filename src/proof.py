"""Доказательство результата: trace, скриншот, видео на сбое.

Паттерн qualityforge:
- Каждый шаг браузера фиксируется: скриншот + метаданные (url, title, timestamp).
- При сбое arтефакты сохраняются в версионированную схему evidence/<task_id>/.
- Агенту доступ read-only: он видит факт, но не может изменить артефакты.
- Единая схема дефекта defect.v1.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)


class EvidenceRecorder:
    """Пишет артефакты выполнения задачи в evidence/<task_id>/."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.dir = Path(settings.EVIDENCE_DIR) / task_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.steps: list = []

    def record_step(
        self,
        step_name: str,
        data: dict[str, Any] | None = None,
        screenshot_path: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "task_id": self.task_id,
            "step": step_name,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data or {},
            "screenshot": screenshot_path,
        }
        self.steps.append(entry)
        self._flush()
        logger.info("Evidence step: %s (%s)", step_name, self.task_id)
        return entry

    def record_failure(self, error: Exception, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Фиксирует сбой по схеме defect.v1."""
        defect = {
            "task_id": self.task_id,
            "status": "failed",
            "error": str(error),
            "error_type": type(error).__name__,
            "timestamp": datetime.now(UTC).isoformat(),
            "steps": self.steps,
            "extra": extra or {},
        }
        path = self.dir / "defect.v1.json"
        path.write_text(json.dumps(defect, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.error("Defect recorded: %s", path)
        return defect

    def record_success(self, result: dict[str, Any]) -> None:
        path = self.dir / "result.v1.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("Success recorded: %s", path)

    def _flush(self) -> None:
        path = self.dir / "steps.jsonl"
        with path.open("a", encoding="utf-8") as f:
            if self.steps:
                f.write(json.dumps(self.steps[-1], ensure_ascii=False) + "\n")

    def get_summary(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "steps_count": len(self.steps),
            "evidence_dir": str(self.dir),
        }


def read_only_evidence(task_id: str) -> dict[str, Any]:
    """Чтение артефактов задачи (read-only доступ для агента).

    Агент может читать, но не записывать — предотвращает донавредить.
    """
    d = Path(settings.EVIDENCE_DIR) / task_id
    if not d.exists():
        return {"task_id": task_id, "exists": False}
    files = [p.name for p in d.iterdir() if p.is_file()]
    return {
        "task_id": task_id,
        "exists": True,
        "files": sorted(files),
        "read_only": True,
    }
