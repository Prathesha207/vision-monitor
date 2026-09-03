"""
debug_routes.py - add this router to your FastAPI app to answer, from a
running server, "which duck_analyzer is actually loaded right now?" --
without needing shell access to the box.

    from debug_routes import router as debug_router
    app.include_router(debug_router)

Then: GET /debug/ml-module-info
"""

import os
import sys
import hashlib
import importlib.metadata as md

from fastapi import APIRouter

router = APIRouter(prefix="/debug", tags=["debug"])


def _file_hash(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:12]
    except Exception:
        return "unreadable"


@router.get("/ml-module-info")
def ml_module_info():
    """
    Reports exactly which duck_analyzer module this running process has
    loaded: its file path, whether that path is an installed wheel
    (site-packages) or a loose .py file, the installed wheel version if
    applicable, a content hash (so you can diff it against a known-good
    file offline), and last-modified time.
    """
    info = {"import_succeeded": False}
    try:
        import duck_analyzer  # whatever's already imported/importable now
        file_path = getattr(duck_analyzer, "__file__", None)
        info.update({
            "import_succeeded": True,
            "file_path": file_path,
            "is_installed_wheel": bool(
                file_path and ("site-packages" in file_path or "dist-packages" in file_path)
            ),
            "last_modified": os.path.getmtime(file_path) if file_path and os.path.exists(file_path) else None,
            "sha256_short": _file_hash(file_path) if file_path else None,
            "has_set_expected_duck_count": hasattr(
                getattr(duck_analyzer, "DuckAnalyzer", object), "set_expected_duck_count"
            ),
        })
        try:
            info["installed_wheel_version"] = md.version("duck_analyzer")
        except md.PackageNotFoundError:
            info["installed_wheel_version"] = None
    except ImportError as e:
        info["error"] = str(e)

    # Every duck_analyzer* file found anywhere currently on sys.path, so you
    # can see every candidate that COULD have been imported, not just the
    # one that won.
    candidates = []
    for p in sys.path:
        if not p or not os.path.isdir(p):
            continue
        try:
            for entry in os.listdir(p):
                if entry.startswith("duck_analyzer"):
                    full = os.path.join(p, entry)
                    candidates.append({
                        "path": full,
                        "last_modified": os.path.getmtime(full) if os.path.exists(full) else None,
                    })
        except PermissionError:
            continue

    info["all_candidates_on_sys_path"] = candidates
    info["sys_path"] = sys.path
    return info