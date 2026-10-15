"""Disposable loopback-only server for browser acceptance tests, never production."""
import argparse
import shutil
import tempfile
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from tunnelui.config import Settings
from tunnelui.main import create_app
from tunnelui.models import Admin
from tunnelui.security import password_hasher

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, choices=[8765, 8766], default=8765)
port = parser.parse_args().port
(root / ".runtime").mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix="e2e-", dir=root / ".runtime") as temporary:
    temporary_path = Path(temporary)
    fixture = temporary_path / "trusttunnel"
    shutil.copytree(root / "dev/fixtures/trusttunnel", fixture)
    url = f"sqlite:///{temporary_path / 'test.db'}"
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    app = create_app(Settings(database_url=url, development=True, secure_cookie=False,
                             origin=f"http://127.0.0.1:{port}", static_dir=root / "frontend/dist",
                             master_key_file=temporary_path / "master.key", sandbox_root=fixture))
    with app.state.sessions() as db:
        db.add(Admin(username="test-admin", password_hash=password_hasher.hash("test-only-password")))
        db.commit()
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)
