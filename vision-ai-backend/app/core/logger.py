import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

# Max 5 MB per log file, keep 3 rotated backups
_MAX_BYTES    = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_shared_file_handler: Optional["HourlyRotatingFileHandler"] = None
_shared_formatter: Optional[logging.Formatter] = None
_hooks_installed: bool = False


class MainApiFilter(logging.Filter):
    """Filters out uvicorn startup/shutdown lifecycle messages, internal HTTP request logs,
    and redundant third-party chatter so the log contains only main API requests, camera/ML operations,
    warnings, errors, and crash traces."""

    UNWANTED_MESSAGES = (
        "Started server process",
        "Waiting for application startup",
        "Application startup complete",
        "Shutting down",
        "Waiting for application shutdown",
        "Application shutdown complete",
        "Finished server process",
        "Vision AI Log Started",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        # Filter out uvicorn INFO lifecycle chatter
        if record.name.startswith("uvicorn") and record.levelno == logging.INFO:
            msg = record.getMessage()
            if any(unwanted in msg for unwanted in self.UNWANTED_MESSAGES):
                return False

        # Filter out httpx / httpcore / multipart internal request lines below WARNING
        if record.name in ("httpx", "httpcore", "multipart", "urllib3"):
            if record.levelno < logging.WARNING:
                return False

        # Filter out raw uvicorn.access lines (handled cleanly by api_logging_middleware)
        if record.name == "uvicorn.access":
            return False

        # Filter out startup banner
        if "Vision AI Log Started" in record.getMessage():
            return False

        return True


class HourlyRotatingFileHandler(logging.Handler):
    """
    Writes log records into date/hour directories: logs/<YYYY-MM-DD>/<HH>/session_<HH>.log.
    Guarantees:
    1. Exactly ONE log file per 1-hour directory (session_<HH>.log).
    2. Subsequent runs and reloads within the same hour append to the same file.
    3. Automatically transitions to a new hour directory when the hour changes.
    4. Rotates backup files (.1, .2) if the file exceeds max_bytes.
    5. Thread-safe writes with a mutex lock.
    """
    def __init__(self, base_log_dir: Path, max_bytes: int = _MAX_BYTES, backup_count: int = _BACKUP_COUNT):
        super().__init__()
        self.base_log_dir = base_log_dir
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._current_date: Optional[str] = None
        self._current_hour: Optional[str] = None
        self._current_file: Optional[Path] = None
        self._stream = None
        self._lock = threading.Lock()

    def get_current_log_path(self) -> Optional[str]:
        if self._current_file:
            return str(self._current_file)
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        hour_str = now.strftime("%H")
        return str(self.base_log_dir / date_str / hour_str / f"session_{hour_str}.log")

    def _get_target_file(self, now: datetime) -> Path:
        date_str = now.strftime("%Y-%m-%d")
        hour_str = now.strftime("%H")
        folder = self.base_log_dir / date_str / hour_str
        folder.mkdir(parents=True, exist_ok=True)
        return folder / f"session_{hour_str}.log"

    def _ensure_stream(self, now: datetime):
        date_str = now.strftime("%Y-%m-%d")
        hour_str = now.strftime("%H")

        if self._stream is None or self._current_date != date_str or self._current_hour != hour_str:
            if self._stream:
                try:
                    self._stream.close()
                except Exception:
                    pass

            self._current_date = date_str
            self._current_hour = hour_str
            self._current_file = self._get_target_file(now)
            self._stream = open(self._current_file, "a", encoding="utf-8")

    def _check_rollover(self):
        if self._current_file and self._current_file.exists():
            if self._current_file.stat().st_size >= self.max_bytes:
                self._stream.close()
                self._stream = None
                for i in range(self.backup_count - 1, 0, -1):
                    sfn = f"{self._current_file}.{i}"
                    dfn = f"{self._current_file}.{i + 1}"
                    if os.path.exists(sfn):
                        if os.path.exists(dfn):
                            os.remove(dfn)
                        os.rename(sfn, dfn)
                dfn = f"{self._current_file}.1"
                if os.path.exists(dfn):
                    os.remove(dfn)
                os.rename(str(self._current_file), dfn)
                self._stream = open(self._current_file, "a", encoding="utf-8")

    def emit(self, record: logging.LogRecord):
        try:
            with self._lock:
                now = datetime.fromtimestamp(record.created)
                self._ensure_stream(now)
                self._check_rollover()
                msg = self.format(record)
                self._stream.write(msg + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        with self._lock:
            if self._stream:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        super().close()


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
    """Return the absolute path of the active hourly session log file."""
    if _shared_file_handler:
        return _shared_file_handler.get_current_log_path()
    return None


def setup_logger(name: str = "vision-ai") -> logging.Logger:
    """
    Initialize and return a logger connected to the hourly rotating log file:
    logs/<YYYY-MM-DD>/<HH>/session_<HH>.log.
    Guarantees:
    - Only ONE log file per 1-hour directory.
    - Zero duplicate log lines.
    - Filters out startup/shutdown noise and internal third-party chatter.
    - Captures main API requests, camera/ML operations, and all crashes/errors.
    """
    global _shared_file_handler, _shared_formatter

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if _shared_file_handler is None:
        backend_root = Path(__file__).resolve().parent.parent.parent
        base_log_dir = backend_root / "logs"

        _shared_file_handler = HourlyRotatingFileHandler(
            base_log_dir,
            max_bytes=_MAX_BYTES,
            backup_count=_BACKUP_COUNT,
        )

        _shared_formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s"
        )
        _shared_file_handler.setFormatter(_shared_formatter)
        _shared_file_handler.addFilter(MainApiFilter())

        # Attach file handler ONLY to root logger so every record is written once
        root_logger.addHandler(_shared_file_handler)

        # Attach console StreamHandler if not present
        if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, HourlyRotatingFileHandler) for h in root_logger.handlers):
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setFormatter(_shared_formatter)
            stream_handler.addFilter(MainApiFilter())
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

    logger.propagate = True
    return logger
