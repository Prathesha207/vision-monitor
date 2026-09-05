import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Optional

# Max 5 MB per log file, keep 3 rotated backups → max 20 MB per session
_MAX_BYTES    = 5 * 1024 * 1024
_BACKUP_COUNT = 3

_shared_handler: Optional[RotatingFileHandler] = None


def get_current_log_path() -> Optional[str]:
    if _shared_handler:
        return _shared_handler.baseFilename
    return None


def setup_logger(name: str = "vision-ai") -> logging.Logger:
    global _shared_handler

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if _shared_handler is None:
        base_log_dir = "logs"

        date_folder = datetime.now().strftime("%Y-%m-%d")
        hour_folder = datetime.now().strftime("%H")

        full_path = os.path.join(base_log_dir, date_folder, hour_folder)
        os.makedirs(full_path, exist_ok=True)

        # Exactly ONE log file per 1-hour folder (e.g. session_01.log)
        log_file_path = os.path.join(full_path, f"session_{hour_folder}.log")

        _shared_handler = RotatingFileHandler(
            log_file_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
        _shared_handler.setFormatter(formatter)

        # Silence unwanted uvicorn startup/shutdown lifecycle messages & httpx noise
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    if _shared_handler not in logger.handlers:
        logger.addHandler(_shared_handler)

    return logger
