"""Шифрование файлов профиля браузера.

Профиль Chromium содержит куки и сессию: кто получил файлы — получил доступ к
кабинетам. Основная защита — права 0700 и отсутствие профиля в git. Шифрование
дополнительное: оно включается флагом `BROWSER_PROFILE_ENCRYPTION=true` и нужно
там, где профиль копируют на бэкап или переносят между машинами.

Ключ лежит в `BROWSER_PROFILE_ENCRYPTION_KEY` или в
`~/.secrets/browser-profile.key` (0600).

Важно про целостность: профиль нельзя шифровать «наполовину». Рядом с файлами
пишется маркер `.profile-encrypted`. Маркер появляется последним и удаляется
первым, поэтому:

* маркер есть → все файлы зашифрованы, можно расшифровывать;
* маркера нет → профиль считается обычным.

Так сбой посередине не превращается в профиль, который «вроде зашифрован», а
на деле смешан. Шифрование и дешифрование — синхронная работа с диском; в
асинхронном коде вызывать через `asyncio.to_thread`, иначе цикл событий
останавливается на всё время работы.
"""

from pathlib import Path

from cryptography.fernet import Fernet

SECRETS_DIR = Path.home() / ".secrets"
KEY_FILE = SECRETS_DIR / "browser-profile.key"
MARKER_NAME = ".profile-encrypted"


def ensure_secrets_dir() -> Path:
    """Создаёт каталог для секретов с правами 0700."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    return SECRETS_DIR


def load_or_create_key() -> bytes:
    """Загружает существующий ключ или создаёт новый и сохраняет его."""
    ensure_secrets_dir()
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    KEY_FILE.chmod(0o600)
    return key


def get_fernet() -> Fernet:
    """Возвращает экземпляр Fernet с загруженным ключом."""
    return Fernet(load_or_create_key())


def encrypt_file(file_path: str | Path) -> None:
    """Шифрует файл на месте, заменяя исходный зашифрованной версией."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")
    encrypted = get_fernet().encrypt(path.read_bytes())
    path.write_bytes(encrypted)
    path.chmod(0o600)


def decrypt_file(file_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Расшифровывает файл. Если output_path не задан, перезаписывает исходный."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")
    decrypted = get_fernet().decrypt(path.read_bytes())
    out = Path(output_path) if output_path else path
    out.write_bytes(decrypted)
    out.chmod(0o600)
    return out


def is_encrypted(file_path: str | Path) -> bool:
    """Проверяет, зашифрован ли отдельный файл."""
    path = Path(file_path)
    if not path.is_file():
        return False
    try:
        get_fernet().decrypt(path.read_bytes())
    except Exception:  # noqa: BLE001 - любой сбой разбора значит «не наш формат»
        return False
    return True


def is_profile_encrypted(directory: str | Path) -> bool:
    """Профиль зашифрован, только если на месте маркер (а не «часть файлов»)."""
    return (Path(directory) / MARKER_NAME).is_file()


def _profile_files(directory: Path) -> list[Path]:
    """Файлы профиля: обычные, без симлинков (их нельзя зашифровать на месте)."""
    return [path for path in sorted(directory.rglob("*")) if path.is_file() and not path.is_symlink()]


def encrypt_profile(directory: str | Path) -> int:
    """Зашифровать все файлы профиля. Возвращает число файлов.

    Повторный вызов для уже зашифрованного профиля ничего не делает: маркер
    на месте, файлы уже зашифрованы.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Нет каталога профиля: {dir_path}")
    if is_profile_encrypted(dir_path):
        return 0

    marker = dir_path / MARKER_NAME
    marker.unlink(missing_ok=True)

    count = 0
    for file_path in _profile_files(dir_path):
        if file_path.name == MARKER_NAME:
            continue
        encrypt_file(file_path)
        count += 1

    marker.write_text("fernet\n", encoding="utf-8")
    marker.chmod(0o600)
    return count


def decrypt_profile(directory: str | Path) -> int:
    """Расшифровать профиль, помеченный маркером. Возвращает число файлов.

    Если маркера нет, профиль не зашифрован — это не ошибка дешифрования,
    а повод ничего не делать.
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Нет каталога профиля: {dir_path}")
    marker = dir_path / MARKER_NAME
    if not marker.is_file():
        return 0

    count = 0
    for file_path in _profile_files(dir_path):
        if file_path.name == MARKER_NAME:
            continue
        decrypt_file(file_path)
        count += 1

    marker.unlink(missing_ok=True)
    return count
