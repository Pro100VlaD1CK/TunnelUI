import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from cryptography.fernet import InvalidToken
from pydantic import ValidationError

from tunnelui.config import Settings
from tunnelui.db import Base, database
from tunnelui.domain.rules import Rule
from tunnelui.security import SecretBox, create_key


def test_secret_encryption_separate_key(tmp_path):
    path = tmp_path / "master.key"
    create_key(path)
    box = SecretBox(path)
    encrypted = box.encrypt("sensitive-value")
    assert "sensitive-value" not in encrypted
    assert box.decrypt(encrypted) == "sensitive-value"
    with pytest.raises(FileExistsError):
        create_key(path)
    other = tmp_path / "other.key"
    create_key(other)
    with pytest.raises(InvalidToken):
        SecretBox(other).decrypt(encrypted)


@pytest.mark.parametrize("data", [
    {"cidr": "garbage", "action": "allow"}, {"cidr": "10.0.0.1/24", "action": "deny"},
    {"client_random_prefix": "xx", "action": "allow"},
    {"client_random_prefix": "aa/ffff", "action": "allow"},
    {"client_random_prefix": "a", "action": "allow"}, {"action": "drop"},
    {"username": "not-supported", "action": "allow"},
])
def test_rules_invalid(data):
    with pytest.raises(ValidationError):
        Rule(**data)


def test_rule_valid():
    assert Rule(cidr="10.0.0.0/8", client_random_prefix="a0b0/f0f0", action="deny").action == "deny"
    assert Rule(action="deny").cidr is None  # explicit catch-all


def test_secure_config_default():
    assert Settings(_env_file=None).secure_cookie
    with pytest.raises(ValidationError):
        Settings(_env_file=None, secure_cookie=False, origin="http://public.example", development=True)


def test_migration_matches_models_and_roundtrip(settings, migrated):
    engine, _ = database(settings.database_url)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()
    command.downgrade(migrated, "base")
    command.upgrade(migrated, "head")
