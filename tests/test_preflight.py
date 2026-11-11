"""Тесты предстартовой проверки: что считается заполненным, а что нет.

Скрипт обязан работать до заполнения .env, поэтому проверяем именно
разбор файла и отчёт, а не импорт src.config.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load("preflight", "scripts/preflight.py")


def test_read_env_skips_comments_and_strips_quotes(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# комментарий\nBOT_TOKEN='123:abc'\n\nDB_PASSWORD=\"secret value\"\n",
        encoding="utf-8",
    )

    env = preflight.read_env(env_file)

    assert env == {"BOT_TOKEN": "123:abc", "DB_PASSWORD": "secret value"}


def test_read_env_missing_file_returns_empty(tmp_path):
    assert preflight.read_env(tmp_path / "missing.env") == {}


@pytest.mark.parametrize(
    "value",
    [None, "", "  ", "change-me", "change-me-strong-db-password", "123456789"],
)
def test_placeholder_values_are_not_filled(value):
    assert preflight.is_filled(value) is False


def test_real_value_is_filled():
    assert preflight.is_filled("real-password-42") is True


def test_check_required_names_each_missing_item():
    checks = preflight.check_required({})

    failed = {check.name for check in checks if not check.ok}
    assert failed == {"BOT_TOKEN", "BOT_ADMIN_ID", "DB_PASSWORD", "REDIS_PASSWORD"}
    assert all(check.detail for check in checks if not check.ok)


def test_check_required_rejects_wrong_token_format():
    env = {
        "BOT_TOKEN": "not-a-token",
        "BOT_ADMIN_ID": "42",
        "DB_PASSWORD": "db-pass",
        "REDIS_PASSWORD": "redis-pass",
    }

    checks = {check.name: check for check in preflight.check_required(env)}

    assert checks["BOT_TOKEN"].ok is False
    assert "формат" in checks["BOT_TOKEN"].detail
    assert checks["BOT_ADMIN_ID"].ok is True


def test_check_required_rejects_non_numeric_admin_id():
    env = {
        "BOT_TOKEN": "123456789:" + "A" * 35,
        "BOT_ADMIN_ID": "algtro",
        "DB_PASSWORD": "db-pass",
        "REDIS_PASSWORD": "redis-pass",
    }

    checks = {check.name: check for check in preflight.check_required(env)}

    assert checks["BOT_ADMIN_ID"].ok is False
    assert checks["BOT_TOKEN"].ok is True


def _patch_environment(monkeypatch):
    """Заменить проверки, которым нужен браузер и права на каталоги."""
    monkeypatch.setattr(preflight, "check_playwright", lambda: preflight.Check("Chromium", True, "заглушка"))


def test_run_returns_zero_when_required_data_present(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_TOKEN=123456789:" + "A" * 35 + "\n"
        "BOT_ADMIN_ID=42\n"
        "DB_PASSWORD=db-pass\n"
        "REDIS_PASSWORD=redis-pass\n"
        f"EVIDENCE_DIR={tmp_path / 'evidence'}\n"
        "LLM_API_KEY=sk-test\n",
        encoding="utf-8",
    )
    _patch_environment(monkeypatch)

    code = preflight.run(env_file)

    output = capsys.readouterr().out
    assert code == 0
    assert "Можно запускать" in output


def test_run_returns_one_and_names_missing_field(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"BOT_ADMIN_ID=42\nDB_PASSWORD=db-pass\nEVIDENCE_DIR={tmp_path / 'evidence'}\n",
        encoding="utf-8",
    )
    _patch_environment(monkeypatch)

    code = preflight.run(env_file)

    output = capsys.readouterr().out
    assert code == 1
    assert "BOT_TOKEN" in output
    assert "не хватает" in output


def test_run_warns_but_passes_without_llm_key(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "BOT_TOKEN=123456789:" + "A" * 35 + "\n"
        "BOT_ADMIN_ID=42\n"
        "DB_PASSWORD=db-pass\n"
        "REDIS_PASSWORD=redis-pass\n"
        f"EVIDENCE_DIR={tmp_path / 'evidence'}\n",
        encoding="utf-8",
    )
    _patch_environment(monkeypatch)

    code = preflight.run(env_file)

    output = capsys.readouterr().out
    assert code == 0
    assert "LLM_API_KEY" in output
    assert "ВНИМАНИЕ" in output
