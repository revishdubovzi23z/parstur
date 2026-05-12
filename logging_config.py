import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from settings import settings

# Cache file handlers to avoid multiple instances writing to the same file
# and interfering with rotation.
_file_handlers = {}


def setup_logging(name: str, log_file: str | None = None) -> logging.Logger:
    """Initialize and return a logger with unified configuration.

    - ISO 8601 UTC timestamp
    - Rotating file handler (50MB, 3 backups)
    - Stdout handler
    """
    logger = logging.getLogger(name)

    # If handlers are already configured, don't add more.
    if logger.handlers:
        return logger

    # Force UTC for logging timestamps.
    logging.Formatter.converter = time.gmtime

    # Formatter: <UTC ISO> <LEVEL> <component> | <message>
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )

    # Stream Handler for console output
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)

    # Rotating File Handler for persistent logs
    if log_file:
        if log_file not in _file_handlers:
            handler = RotatingFileHandler(
                log_file,
                maxBytes=50 * 1024 * 1024,  # 50 MB
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(formatter)
            _file_handlers[log_file] = handler
        logger.addHandler(_file_handlers[log_file])

    # Log level from settings
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    logger.setLevel(level_map.get(settings.log_level.upper(), logging.INFO))

    return logger
