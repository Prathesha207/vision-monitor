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


def get_desktop_dir() -> Path | None:
    """Resolve Desktop robustly, handling Windows Registry, OneDrive redirection, and Linux paths."""
    if sys.platform == "win32":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            val, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            expanded = os.path.expandvars(val)
            if os.path.exists(expanded):
                return Path(expanded)
        except Exception:
            pass

    home = Path.home()
    for candidate in [home / "OneDrive" / "Desktop", home / "Desktop"]:
        if candidate.exists() and candidate.is_dir():
            return candidate

    return None


APP_DIR = _user_data_dir()

APP_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = APP_DIR / "vision_ai.db"
