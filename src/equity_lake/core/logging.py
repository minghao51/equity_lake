"""
Structured Logging Utilities

This module provides structured logging with JSON output, correlation IDs,
and automatic timing metrics for better observability.
"""

import contextvars
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Dict, Generator, Optional

import structlog

# Context variable for correlation ID (request tracking)
_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def get_correlation_id() -> str:
    """Get or create correlation ID for request tracking."""
    cid = _correlation_id.get()
    if not cid:
        cid = str(uuid.uuid4())[:8]
        _correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str) -> None:
    """Set correlation ID for request tracking."""
    _correlation_id.set(cid)


@contextmanager
def correlation_context(correlation_id: Optional[str] = None) -> Generator[None, None, None]:
    """
    Context manager for correlation ID scope.

    Usage:
        with correlation_context("abc123"):
            logger.info("This log will have correlation_id=abc123")
    """
    cid = correlation_id or str(uuid.uuid4())[:8]
    token = _correlation_id.set(cid)
    try:
        yield
    finally:
        _correlation_id.reset(token)


def add_correlation_id(logger, method_name, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Structlog processor to add correlation ID to all log entries.
    """
    event_dict["correlation_id"] = get_correlation_id()
    return event_dict


def add_timestamp(logger, method_name, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Structlog processor to add ISO-format timestamp.
    """
    event_dict["timestamp"] = datetime.utcnow().isoformat() + "Z"
    return event_dict


def drop_color_message_key(logger, method_name, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Structlog processor to remove color message keys for clean JSON output.
    """
    event_dict.pop("color_message", None)
    return event_dict


def setup_structured_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    json_output: bool = True,
    console: bool = True
) -> structlog.stdlib.BoundLogger:
    """
    Configure structured logging with structlog.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional path to log file
        json_output: If True, output JSON logs; otherwise human-readable
        console: If True, log to console

    Returns:
        Configured structlog bound logger

    Example:
        >>> logger = setup_structured_logging(level="INFO", log_file=Path("logs/app.log"))
        >>> logger.info("fetch_started", market="us", ticker_count=500)
    """
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout if console else None,
        level=getattr(logging, level.upper()),
    )

    # Build structlog processors
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        add_correlation_id,
        add_timestamp,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Add output formatter
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Human-readable console output
        processors.append(drop_color_message_key)
        processors.append(
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback
            )
        )

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Get logger
    logger = structlog.get_logger()

    # Add file handler if specified
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(getattr(logging, level.upper()))

        if json_output:
            # JSON output for files
            file_formatter = logging.Formatter("%(message)s")
        else:
            # Human-readable for files
            file_formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )

        file_handler.setFormatter(file_formatter)

        # Add handler to root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)

    return logger


# =============================================================================
# Timing Metrics Decorator
# =============================================================================

def timed(logger: Optional[structlog.stdlib.BoundLogger] = None, **log_kwargs):
    """
    Decorator to automatically log function execution time.

    Args:
        logger: Structlog logger instance (uses new logger if None)
        **log_kwargs: Additional context to log with timing info

    Example:
        >>> @timed()
        >>> def fetch_data(ticker: str):
        >>>     # ... fetch logic
        >>>     return data
        >>>
        >>> # Output: {"event": "fetch_data_completed", "duration_seconds": 1.23, "ticker": "AAPL"}

    Example with custom logger:
        >>> logger = setup_structured_logging()
        >>> @timed(logger, market="us")
        >>> def fetch_market_data(date: str):
        >>>     return data
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _logger = logger or structlog.get_logger()
            func_name = func.__name__

            start_time = time.time()
            _logger.debug(
                f"{func_name}_started",
                function=func_name,
                **log_kwargs
            )

            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time

                _logger.info(
                    f"{func_name}_completed",
                    function=func_name,
                    duration_seconds=round(duration, 3),
                    status="success",
                    **log_kwargs
                )

                return result

            except Exception as e:
                duration = time.time() - start_time

                _logger.error(
                    f"{func_name}_failed",
                    function=func_name,
                    duration_seconds=round(duration, 3),
                    status="error",
                    error=str(e),
                    error_type=type(e).__name__,
                    **log_kwargs
                )
                raise

        return wrapper
    return decorator


@contextmanager
def timer(
    operation_name: str,
    logger: Optional[structlog.stdlib.BoundLogger] = None,
    **log_kwargs
) -> Generator[None, None, None]:
    """
    Context manager to time an operation and log duration.

    Args:
        operation_name: Name of the operation being timed
        logger: Structlog logger instance
        **log_kwargs: Additional context to log

    Example:
        >>> with timer("data_ingestion", market="us"):
        >>>     # ... perform operation
        >>>
        >>> # Output: {"event": "data_ingestion_completed", "duration_seconds": 5.43, "market": "us"}
    """
    _logger = logger or structlog.get_logger()
    start_time = time.time()

    _logger.debug(
        f"{operation_name}_started",
        operation=operation_name,
        **log_kwargs
    )

    try:
        yield

    finally:
        duration = time.time() - start_time
        _logger.info(
            f"{operation_name}_completed",
            operation=operation_name,
            duration_seconds=round(duration, 3),
            **log_kwargs
        )


# =============================================================================
# Backward Compatibility Wrapper
# =============================================================================

def setup_logging(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Backward-compatible wrapper for setup_logging.

    This maintains the original API while using structlog internally.

    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file name (will be placed in LOGS_DIR)

    Returns:
        Logger instance (standard library logger for backward compatibility)
    """
    from equity_lake.core.runtime import LOGS_DIR

    # Determine if we should use JSON output (check environment or default to True)
    json_output = True  # Default to JSON for production

    # Convert log_file string to Path if provided
    log_path = None
    if log_file:
        log_path = LOGS_DIR / log_file

    # Setup structured logging
    structlog_logger = setup_structured_logging(
        level=level,
        log_file=log_path,
        json_output=json_output,
        console=True
    )

    # Return standard library logger for backward compatibility
    stdlib_logger = logging.getLogger(name)
    return stdlib_logger


__all__ = [
    "setup_structured_logging",
    "setup_logging",
    "timed",
    "timer",
    "correlation_context",
    "get_correlation_id",
    "set_correlation_id",
]
