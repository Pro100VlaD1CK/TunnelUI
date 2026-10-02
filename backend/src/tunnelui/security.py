import hashlib
import os
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet

password_hasher = PasswordHasher()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_password(encoded: str, supplied: str) -> bool:
    try:
        return password_hasher.verify(encoded, supplied)
    except (VerificationError, InvalidHashError):
        return False


class SecretBox:
    def __init__(self, key_file: Path):
        if key_file.is_symlink():
            raise ValueError("master key must be a regular private file")
        if os.name == "posix" and key_file.stat().st_mode & 0o077:
            raise ValueError("master key permissions must be 0600")
        self._fernet = Fernet(key_file.read_bytes().strip())

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()


def create_key(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(Fernet.generate_key())
        stream.flush()
        os.fsync(stream.fileno())
