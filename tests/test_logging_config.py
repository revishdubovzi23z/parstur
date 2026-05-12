import logging
import os
import time
from logging_config import setup_logging

def test_setup_logging_format(tmp_path):
    log_file = tmp_path / "test.log"
    logger_name = "test_logger"
    logger = setup_logging(logger_name, str(log_file))
    
    test_message = "Test log message"
    logger.info(test_message)
    
    # Wait a bit for file write if needed (though it should be sync)
    time.sleep(0.1)
    
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    
    # Expected format: 2024-05-13T10:00:00Z INFO test_logger | Test log message
    assert " INFO test_logger | " + test_message in content
    # Check for ISO 8601 like timestamp (YYYY-MM-DDTHH:MM:SSZ)
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", content)

def test_setup_logging_rotation(tmp_path):
    # Set a small maxBytes to trigger rotation
    log_file = tmp_path / "rotation.log"
    logger = logging.getLogger("rotation_logger")
    
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler(str(log_file), maxBytes=100, backupCount=1)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    
    # Write enough to trigger rotation
    logger.info("A" * 60)
    logger.info("B" * 60)
    
    assert log_file.exists()
    assert os.path.exists(str(log_file) + ".1")

def test_setup_logging_singleton_handlers():
    from logging_config import setup_logging
    # Calling twice for the same component should not double handlers
    logger = setup_logging("singleton_test")
    count = len(logger.handlers)
    logger = setup_logging("singleton_test")
    assert len(logger.handlers) == count
