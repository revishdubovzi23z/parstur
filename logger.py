import logging
import sys

from settings import settings


def setup_logger(name: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
    }
    logger.setLevel(level_map.get(settings.log_level, logging.INFO))
    return logger


class TeeWriter:
    def __init__(self, log: logging.Logger, stream=None):
        self.log = log
        self.stream = stream or sys.__stdout__

    def write(self, message):
        self.stream.write(message)
        if message and message.strip():
            self.log.info(message.rstrip("\n"))

    def flush(self):
        self.stream.flush()


def setup_tee_logger(name: str, log_file: str) -> logging.Logger:
    log = setup_logger(name, log_file)
    # Tee both stdout and stderr. Previously only stdout was captured,
    # which meant tracebacks and any stderr-routed errors only landed
    # in the live console and were lost to the persistent log — the
    # one place a user is likely to look after the process has ended.
    sys.stdout = TeeWriter(log, sys.__stdout__)
    sys.stderr = TeeWriter(log, sys.__stderr__)
    return log
