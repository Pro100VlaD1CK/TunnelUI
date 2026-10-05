from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TUNNELUI_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./.runtime/tunnelui.db"
    master_key_file: Path = Path(".runtime/master.key")
    origin: str = "https://localhost:8443"
    secure_cookie: bool = True
    development: bool = False
    session_seconds: int = 28800
    static_dir: Path = Path("frontend/dist")
    sandbox_root: Path | None = None

    @model_validator(mode="after")
    def secure_defaults(self):
        from urllib.parse import urlsplit

        parsed = urlsplit(self.origin)
        if not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("origin must be a scheme and authority only")
        if not self.secure_cookie and not (
            self.development and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("insecure cookies require explicit loopback development")
        if self.secure_cookie and parsed.scheme != "https":
            raise ValueError("secure mode requires HTTPS origin")
        if self.sandbox_root is not None and not self.development:
            raise ValueError("sandbox_root requires explicit development mode")
        return self
