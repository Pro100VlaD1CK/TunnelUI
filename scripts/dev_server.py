"""Loopback-only reproducible sandbox server for manual Phase 2 walkthroughs."""
import argparse
import shutil
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from tunnelui.config import Settings
from tunnelui.main import create_app
from tunnelui.models import Admin
from tunnelui.security import password_hasher

parser = argparse.ArgumentParser()
parser.add_argument("--username", default="admin")
parser.add_argument("--password", required=True)
args = parser.parse_args()
if len(args.password) < 12:
    parser.error("--password must contain at least 12 characters")

root = Path(__file__).resolve().parents[1]
runtime = root / ".runtime"
sandbox = runtime / "dev-trusttunnel"
runtime.mkdir(exist_ok=True)
if not sandbox.exists():
    shutil.copytree(root / "dev/fixtures/trusttunnel", sandbox)
url = f"sqlite:///{runtime / 'dev.db'}"
config = Config(str(root / "backend/alembic.ini"))
config.set_main_option("script_location", str(root / "backend/migrations"))
config.set_main_option("sqlalchemy.url", url)
command.upgrade(config, "head")
app = create_app(Settings(
    database_url=url, development=True, secure_cookie=False,
    origin="http://127.0.0.1:8765", static_dir=root / "frontend/dist",
    master_key_file=runtime / "dev-master.key", sandbox_root=sandbox,
))
with app.state.sessions() as db:
    if not db.query(Admin).first():
        db.add(Admin(username=args.username, password_hash=password_hasher.hash(args.password)))
        db.commit()
uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)
