import argparse
import getpass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from tunnelui.config import Settings
from tunnelui.db import database
from tunnelui.models import Admin
from tunnelui.security import create_key, password_hasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate", "create-admin", "create-key", "expiry-run"])
    args = parser.parse_args()
    settings = Settings()
    if args.command == "create-key":
        create_key(settings.master_key_file)
        print("Master key created; keep a separate secure backup.")
        return
    if args.command == "migrate":
        if settings.database_url.startswith("sqlite:///"):
            Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        root = Path(__file__).resolve().parents[2]
        config = Config(str(root / "alembic.ini"))
        config.set_main_option("script_location", str(root / "migrations"))
        config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
        command.upgrade(config, "head")
        return
    if args.command == "expiry-run":
        from tunnelui.main import create_app
        from tunnelui.services.expiry import ExpiryScheduler

        app = create_app(settings)
        runtime = app.state.management
        if runtime is None:
            raise SystemExit("Managed TrustTunnel runtime is not configured")
        try:
            scheduler = ExpiryScheduler(runtime.coordinator)
            if not scheduler.preview()["enabled"]:
                print("Expiry scan disabled; no host operation requested")
                return
            runtime.coordinator.recover_all()
            result = scheduler.run_once()
            print(f"Expiry scan: {result['processed']} processed, "
                  f"{result['succeeded']} confirmed, {result['errors']} unresolved")
        finally:
            app.state.engine.dispose()
        return
    engine, factory = database(settings.database_url)
    try:
        with factory() as db:
            if db.scalar(select(Admin.id).limit(1)):
                raise SystemExit("Admin already exists; bootstrap refused.")
            username = input("Admin username: ").strip()
            password = getpass.getpass("Password (minimum 12 characters): ")
            if not username or len(username) > 64 or len(password) < 12 or len(password) > 1024:
                raise SystemExit("Invalid username or password length")
            if password != getpass.getpass("Repeat password: "):
                raise SystemExit("Passwords differ")
            db.add(Admin(username=username, password_hash=password_hasher.hash(password)))
            db.commit()
            print("Admin created")
    finally:
        engine.dispose()
