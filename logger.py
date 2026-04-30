import logging
import sys


def setup_logger(name: str, log_file: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
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
    sys.stdout = TeeWriter(log)
    return log
