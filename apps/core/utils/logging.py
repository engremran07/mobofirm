from __future__ import annotations

import logging
from typing import Any


def get_logger(name: str) -> logging.Logger:
    """
    Centralized logger factory to keep a consistent format across apps.
    """
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: str, message: str, **extra: Any) -> None:
    """
    Structured logging helper. Adds 'event' payload via `extra` without
    raising if the logger is misconfigured.
    """
    try:
        if level == "debug":
            logger.debug(message, extra={"event": extra})
        elif level == "info":
            logger.info(message, extra={"event": extra})
        elif level == "warning":
            logger.warning(message, extra={"event": extra})
        elif level == "error":
            logger.error(message, extra={"event": extra})
        else:
            logger.log(logging.INFO, message, extra={"event": extra})
    except Exception:
        # Never let logging break app flow
        return
