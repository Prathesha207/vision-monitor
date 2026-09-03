from pathlib import Path
import os
import sys


def _user_data_dir() -> Path:
    """Return a writable per-user data directory on every supported OS."""
    if sys.platform == "win32":
        root = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        return Path(root or Path.home()) / "Vision-AI"

    # XDG is standard on Ubuntu and other Linux desktop distributions.
    return Path(os.getenv("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "vision-ai"


APP_DIR = _user_data_dir()

APP_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = APP_DIR / "vision_ai.db"
