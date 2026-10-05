import shutil
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


def migrate(settings):
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(config, "head")


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


@pytest.fixture
def sandbox_settings(tmp_path):
    source = Path(__file__).resolve().parents[2] / "dev/fixtures/trusttunnel"
    fixture = tmp_path / "trusttunnel"
    shutil.copytree(source, fixture)
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'sandbox.db'}", origin="https://testserver",
        master_key_file=tmp_path / "sandbox.key", static_dir=tmp_path / "absent",
        development=True, sandbox_root=fixture,
    )


@pytest.fixture
def sandbox_app(sandbox_settings):
    migrate(sandbox_settings)
    application = create_app(sandbox_settings)
    with application.state.sessions() as db:
        db.add(Admin(username="admin", password_hash=password_hasher.hash("test-only-password")))
        db.commit()
    return application


@pytest.fixture
def sandbox_authenticated(sandbox_app):
    with TestClient(sandbox_app, base_url="https://testserver") as client:
        csrf = client.get("/api/auth/csrf").json()["csrf_token"]
        response = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-only-password"},
            headers={"Origin": "https://testserver", "X-CSRF-Token": csrf},
        )
        client.headers.update({
            "Origin": "https://testserver", "X-CSRF-Token": response.json()["csrf_token"],
        })
        yield client
