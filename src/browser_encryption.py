"""Шифрование профиля браузера и чувствительных файлов.

Использует симметричное шифрование Fernet (AES-128 в режиме CBC с HMAC-SHA256).
Ключ хранится в переменной окружения BROWSER_PROFILE_ENCRYPTION_KEY или
генерируется один раз и сохраняется в ~/.secrets/browser-profile.key.

При первом запуске создаёт ключ, если его нет. Все последующие запуски
используют существующий ключ для расшифровки/шифрования профиля.
"""

from pathlib import Path

from cryptography.fernet import Fernet

SECRETS_DIR = Path.home() / ".secrets"
KEY_FILE = SECRETS_DIR / "browser-profile.key"


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
    key = load_or_create_key()
    return Fernet(key)


def encrypt_file(file_path: str | Path) -> None:
    """Шифрует файл на месте, заменяя исходный зашифрованной версией."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    fernet = get_fernet()
    data = path.read_bytes()
    encrypted = fernet.encrypt(data)
    path.write_bytes(encrypted)
    path.chmod(0o600)


def decrypt_file(file_path: str | Path, output_path: str | Path | None = None) -> Path:
    """Расшифровывает файл. Если output_path не задан, перезаписывает исходный."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    fernet = get_fernet()
    data = path.read_bytes()
    decrypted = fernet.decrypt(data)
    out = Path(output_path) if output_path else path
    out.write_bytes(decrypted)
    out.chmod(0o600)
    return out


def is_encrypted(file_path: str | Path) -> bool:
    """Проверяет, зашифрован ли файл (пытается расшифровать заголовок)."""
    try:
        path = Path(file_path)
        if not path.exists():
            return False
        fernet = get_fernet()
        data = path.read_bytes()
        fernet.decrypt(data)
        return True
    except Exception:
        return False


def encrypt_directory_contents(directory: str | Path) -> list[str]:
    """Шифрует все файлы в директории рекурсивно. Возвращает список зашифрованных."""
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    encrypted = []
    for file_path in dir_path.rglob("*"):
        if file_path.is_file():
            encrypt_file(str(file_path))
            encrypted.append(str(file_path))
    return encrypted


def decrypt_directory_contents(directory: str | Path) -> list[str]:
    """Расшифровывает все файлы в директории рекурсивно. Возвращает список расшифрованных."""
    dir_path = Path(directory)
    if not dir_path.exists():
        return []
    decrypted = []
    for file_path in dir_path.rglob("*"):
        if file_path.is_file():
            decrypt_file(str(file_path))
            decrypted.append(str(file_path))
    return decrypted
