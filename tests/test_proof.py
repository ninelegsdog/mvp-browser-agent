import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.config import settings
from src.proof import EvidenceRecorder, read_only_evidence


def test_evidence_record_step():
    """Запись шага в evidence."""
    with tempfile.TemporaryDirectory() as tmp:
        settings.EVIDENCE_DIR = tmp
        recorder = EvidenceRecorder("t1")
        entry = recorder.record_step("start", {"url": "https://example.com"})
        assert entry["task_id"] == "t1"
        assert entry["step"] == "start"
        assert entry["data"]["url"] == "https://example.com"
        assert "timestamp" in entry


def test_evidence_record_failure():
    """Запись сбоя по схеме defect.v1."""
    with tempfile.TemporaryDirectory() as tmp:
        settings.EVIDENCE_DIR = tmp
        recorder = EvidenceRecorder("t1")
        recorder.record_step("step1", {"x": 1})
        defect = recorder.record_failure(ValueError("test error"), {"extra": "info"})
        assert defect["task_id"] == "t1"
        assert defect["status"] == "failed"
        assert defect["error"] == "test error"
        assert defect["error_type"] == "ValueError"
        assert len(defect["steps"]) == 1
        assert (recorder.dir / "defect.v1.json").exists()


def test_evidence_record_success():
    """Запись успешного результата."""
    with tempfile.TemporaryDirectory() as tmp:
        settings.EVIDENCE_DIR = tmp
        recorder = EvidenceRecorder("t1")
        recorder.record_success({"status": "done"})
        assert (recorder.dir / "result.v1.json").exists()


def test_read_only_evidence_missing():
    """Чтение несуществующих доказательств."""
    with tempfile.TemporaryDirectory() as tmp:
        settings.EVIDENCE_DIR = tmp
        info = read_only_evidence("nonexistent")
        assert info["exists"] is False


def test_read_only_evidence_exists():
    """Чтение существующих доказательств."""
    with tempfile.TemporaryDirectory() as tmp:
        settings.EVIDENCE_DIR = tmp
        recorder = EvidenceRecorder("t1")
        recorder.record_step("start", {})
        info = read_only_evidence("t1")
        assert info["exists"] is True
        assert info["read_only"] is True
        assert "steps.jsonl" in info["files"]


if __name__ == "__main__":
    test_evidence_record_step()
    test_evidence_record_failure()
    test_evidence_record_success()
    test_read_only_evidence_missing()
    test_read_only_evidence_exists()
    print("All proof tests passed")
