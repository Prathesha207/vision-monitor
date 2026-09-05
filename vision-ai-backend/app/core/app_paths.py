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


def get_desktop_dir() -> Path:
    """Resolve Desktop robustly on ANY PC, handling Windows Registry, OneDrive redirection, Linux, and custom user profiles."""
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
            p = Path(expanded)
            if p.exists() and p.is_dir():
                return p
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass

    home = Path.home()
    # Check OneDrive Desktop or Standard Desktop
    for candidate in [home / "OneDrive" / "Desktop", home / "Desktop"]:
        if candidate.exists() and candidate.is_dir():
            return candidate

    # Linux standard XDG desktop check
    xdg_desktop = os.getenv("XDG_DESKTOP_DIR")
    if xdg_desktop:
        try:
            p = Path(xdg_desktop)
            if p.exists() and p.is_dir():
                return p
        except Exception:
            pass

    # Ensure fallback Desktop exists on any PC
    try:
        fallback = home / "Desktop"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
    except Exception:
        fallback = APP_DIR / "Desktop"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


APP_DIR = _user_data_dir()

APP_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = APP_DIR / "vision_ai.db"
