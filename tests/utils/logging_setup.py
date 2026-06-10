# utils/logging_setup.py
import logging
from pathlib import Path
from datetime import datetime
import tempfile
import os

def setup_test_logger(name: str = "test") -> logging.Logger:
    """
    Set up a logger that writes to a timestamped file in a temp directory.
    Returns a configured logger instance.
    """
    # Create a temp directory for logs if it doesn't exist
    log_dir = Path(tempfile.gettempdir()) / "etl_test_logs"
    log_dir.mkdir(exist_ok=True)

    # Generate a timestamped log file name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"{name}_{timestamp}.log"

    # Create a logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)  # Capture INFO and above

    # Clear any existing handlers to avoid duplicate logs
    logger.handlers = []

    # File handler
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)

    # Console handler (optional, for real-time output)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    # Log the file path for reference
    logger.info(f"Test logs are being written to: {log_file}")

    return logger
