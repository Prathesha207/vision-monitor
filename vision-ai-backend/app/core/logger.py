import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

# Max 5 MB per log file, keep 3 rotated backups → max 20 MB per session
_MAX_BYTES    = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def setup_logger(name: str = "vision-ai") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    
    # Check if we already have a RotatingFileHandler to avoid duplicate logs
    if any(isinstance(h, RotatingFileHandler) for h in root_logger.handlers):
        return logger

    root_logger.setLevel(logging.INFO)

    base_log_dir = "logs"

    date_folder = datetime.now().strftime("%Y-%m-%d")
    hour_folder = datetime.now().strftime("%H")

    full_path = os.path.join(base_log_dir, date_folder, hour_folder)
    os.makedirs(full_path, exist_ok=True)

    log_file_path = os.path.join(
        full_path, datetime.now().strftime("session_%Y-%m-%d_%H-%M-%S.log")
    )

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    file_handler.setFormatter(formatter)

    # Add handler to root logger so ALL logs are captured
    root_logger.addHandler(file_handler)

    # We also add a stream handler if not already present
    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)

    logger.info(f"[LOGGER INIT] Log file created: {log_file_path} (max {_MAX_BYTES//1024//1024}MB, {_BACKUP_COUNT} backups)")

    return logger