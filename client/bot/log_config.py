import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Anchor to the repo root so the log is found regardless of CWD.
_BASE_DIR = Path(__file__).resolve().parent.parent


def setup_logging():
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    handler = RotatingFileHandler(
        _BASE_DIR / "bot.log",
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=level,
        format=FORMAT,
        handlers=[
            handler,
            logging.StreamHandler(),  # Optional: also print logs to console
        ],
    )
