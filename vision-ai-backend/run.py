
import os
import shutil
import sys
import uvicorn
from pathlib import Path


def user_data_dir() -> Path:
    if sys.platform == "win32":
        root = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        return Path(root or Path.home()) / "Vision-AI"
    return Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "vision-ai"


DATA_DIR = user_data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_PATH = DATA_DIR / "vision_ai.db"

# Set this before importing app.main: app.core.database reads the setting
# during import. This keeps the database writable on Windows and Linux.
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATABASE_PATH.as_posix()}")

from app.main import app

def resource_path(relative_path):
    """
    Get absolute path for PyInstaller
    """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def setup_db():
    src_db = resource_path("vision_ai.db")
    dst_db = str(DATABASE_PATH)

    # If DB already exists, do nothing
    if os.path.exists(dst_db):
        print("[BOOTSTRAP] Database already exists")
        return

    # Copy bundled DB if available
    if os.path.exists(src_db):
        shutil.copy(src_db, dst_db)
        print(f"[BOOTSTRAP] Database copied from {src_db}")
    else:
        # Create empty DB file
        open(dst_db, "a").close()
        print(f"[BOOTSTRAP] Empty database created at {dst_db}")

if __name__ == "__main__":

    setup_db()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        reload=False
    )
