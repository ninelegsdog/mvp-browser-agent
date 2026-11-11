"""Конфигурация тестов - загружает переменные окружения из .env.test."""

import os
from pathlib import Path

# Загружаем .env.test перед импортом любых модулей проекта
env_test_path = Path(__file__).parent / ".env.test"
if env_test_path.exists():
    with open(env_test_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)
