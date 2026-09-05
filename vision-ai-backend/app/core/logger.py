import logging
import os
import sys
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

# Max 5 MB per log file, keep 3 rotated backups → max 20 MB per session
_MAX_BYTES    = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_shared_file_handler: Optional[RotatingFileHandler] = None
_shared_formatter: Optional[logging.Formatter] = None
_current_log_file_path: Optional[str] = None
_hooks_installed: bool = False


def _install_crash_hooks():
    """Capture any uncaught exceptions across all threads and log full tracebacks to the session log."""
    global _hooks_installed
    if _hooks_installed:
        return

    def _sys_excepthook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        crash_logger = logging.getLogger("vision-ai.crash")
        crash_logger.critical(
            f"[CRASH] Uncaught top-level exception: {exc_value}",
            exc_info=(exc_type, exc_value, exc_traceback)
        )

    sys.excepthook = _sys_excepthook

    # threading.excepthook (Python 3.8+)
    if hasattr(threading, "excepthook"):
        def _thread_excepthook(args):
            if issubclass(args.exc_type, KeyboardInterrupt):
                return
            crash_logger = logging.getLogger("vision-ai.crash")
            thread_name = getattr(args.thread, "name", "unknown_thread")
            crash_logger.critical(
                f"[THREAD CRASH] Uncaught exception in thread '{thread_name}': {args.exc_value}",
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
            )

        threading.excepthook = _thread_excepthook

    _hooks_installed = True


def get_current_log_path() -> Optional[str]:
    """Return the absolute path of the active session log file."""
    return _current_log_file_path


def setup_logger(name: str = "vision-ai") -> logging.Logger:
    """
    Initialize and return a logger connected to the single unified rotating log file: logs/vision_ai.log.
    Guarantees every log line is written EXACTLY ONCE without duplicates or third-party HTTP spam.
    """
    global _shared_file_handler, _shared_formatter, _current_log_file_path

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if _shared_file_handler is None:
        # Single log file directly in logs/
        backend_root = Path(__file__).resolve().parent.parent.parent
        base_log_dir = backend_root / "logs"
        base_log_dir.mkdir(parents=True, exist_ok=True)

        log_file = base_log_dir / "vision_ai.log"
        _current_log_file_path = str(log_file)

        _shared_file_handler = RotatingFileHandler(
            _current_log_file_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )

        _shared_formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s"
        )
        _shared_file_handler.setFormatter(_shared_formatter)

        # Attach file handler ONLY to root logger.
        # Child loggers bubble to root by default, ensuring each line is written EXACTLY ONCE.
        root_logger.addHandler(_shared_file_handler)

        # Attach console StreamHandler to root if not present
        if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers if not isinstance(h, RotatingFileHandler)):
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(_shared_formatter)
            root_logger.addHandler(stream_handler)

        # Install system & thread crash hooks
        _install_crash_hooks()

        # Suppress unwanted internal third-party HTTP/network request spam
        for noisy_lib in ["httpx", "httpcore", "multipart", "urllib3"]:
            logging.getLogger(noisy_lib).setLevel(logging.WARNING)

        # Prevent duplicate handlers on uvicorn
        for uvicorn_name in ["uvicorn", "uvicorn.error", "uvicorn.access"]:
            u_logger = logging.getLogger(uvicorn_name)
            u_logger.propagate = True
            u_logger.handlers.clear()

        # Suppress uvicorn.access since api_logging_middleware already logs clean [API] lines
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

        init_logger = logging.getLogger("vision-ai")
        init_logger.info(
            f"=== Vision AI Log Started === (Log: {_current_log_file_path}, max {_MAX_BYTES//1024//1024}MB, {_BACKUP_COUNT} backups)"
        )

    logger.propagate = True
    return logger
