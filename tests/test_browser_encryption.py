"""Тесты шифрования профиля: маркер, целостность, повторный вызов.

Браузер здесь не нужен: проверяется работа с файлами. Отдельно проверяется,
что профиль без маркера не считается зашифрованным — иначе «наполовину
зашифрованный» профиль молча ломал бы запуск.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.browser_encryption import (  # noqa: E402
    MARKER_NAME,
    decrypt_file,
    decrypt_profile,
    encrypt_file,
    encrypt_profile,
    is_encrypted,
    is_profile_encrypted,
)


def _make_profile(root: Path, files: int = 3) -> Path:
    profile = root / "profile"
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_bytes(b"cookie-bytes")
    (profile / "Default" / "Login Data").write_bytes(b"logins")
    for index in range(files):
        (profile / "Default" / f"file-{index}.bin").write_bytes(f"payload-{index}".encode())
    return profile


def test_plain_profile_is_not_encrypted(tmp_path):
    profile = _make_profile(tmp_path)

    assert is_profile_encrypted(profile) is False


def test_encrypt_then_decrypt_roundtrip(tmp_path):
    profile = _make_profile(tmp_path)
    original = (profile / "Default" / "Cookies").read_bytes()

    count = encrypt_profile(profile)
    encrypted = (profile / "Default" / "Cookies").read_bytes()

    assert count == 5
    assert is_profile_encrypted(profile) is True
    assert encrypted != original
    assert (profile / MARKER_NAME).is_file()

    decrypted_count = decrypt_profile(profile)

    assert decrypted_count == 5
    assert (profile / "Default" / "Cookies").read_bytes() == original
    assert is_profile_encrypted(profile) is False
    assert not (profile / MARKER_NAME).exists()


def test_encrypt_is_idempotent(tmp_path):
    """Повторное шифрование не портит уже зашифрованный профиль."""
    profile = _make_profile(tmp_path)
    encrypt_profile(profile)
    after_first = (profile / "Default" / "Cookies").read_bytes()

    assert encrypt_profile(profile) == 0
    assert (profile / "Default" / "Cookies").read_bytes() == after_first
    assert decrypt_profile(profile) == 5


def test_decrypt_plain_profile_does_nothing(tmp_path):
    """Профиль без маркера — не ошибка: он просто не зашифрован."""
    profile = _make_profile(tmp_path)

    assert decrypt_profile(profile) == 0
    assert (profile / "Default" / "Cookies").read_bytes() == b"cookie-bytes"


def test_encrypt_profile_requires_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        encrypt_profile(tmp_path / "missing")


def test_encrypt_profile_skips_symlinks(tmp_path):
    """Симлинк нельзя зашифровать на месте — его пропускаем, а не ломаем."""
    profile = _make_profile(tmp_path, files=0)
    outside = tmp_path / "outside.txt"
    outside.write_text("не трогать", encoding="utf-8")
    (profile / "Default" / "link").symlink_to(outside)

    encrypt_profile(profile)

    assert outside.read_text(encoding="utf-8") == "не трогать"
    assert decrypt_profile(profile) == 2


def test_single_file_helpers(tmp_path):
    path = tmp_path / "secret.txt"
    path.write_bytes(b"hello")

    assert is_encrypted(path) is False
    encrypt_file(path)
    assert is_encrypted(path) is True

    decrypt_file(path)
    assert path.read_bytes() == b"hello"


def test_is_encrypted_is_false_for_directory(tmp_path):
    """Каталог не может быть «зашифрованным файлом» — это не должно выглядеть успехом."""
    profile = _make_profile(tmp_path)

    assert is_encrypted(profile) is False
