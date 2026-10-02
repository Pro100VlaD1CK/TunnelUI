from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from tunnelui.config import Settings
from tunnelui.main import create_app
from tunnelui.models import Admin
from tunnelui.security import password_hasher


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", origin="https://testserver",
                    master_key_file=tmp_path / "master.key", static_dir=tmp_path / "absent")


@pytest.fixture
def migrated(settings):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")
    return config


@pytest.fixture
def app(settings, migrated):
    application = create_app(settings)
    with application.state.sessions() as db:
        db.add(Admin(username="admin", password_hash=password_hasher.hash("test-only-password")))
        db.commit()
    return application


@pytest.fixture
def http(app):
    with TestClient(app, base_url="https://testserver") as client:
        yield client


@pytest.fixture
def authenticated(http):
    csrf = http.get("/api/auth/csrf").json()["csrf_token"]
    response = http.post("/api/auth/login", json={"username": "admin", "password": "test-only-password"},
                         headers={"Origin": "https://testserver", "X-CSRF-Token": csrf})
    assert response.status_code == 200
    http.headers.update({"Origin": "https://testserver", "X-CSRF-Token": response.json()["csrf_token"]})
    return http
