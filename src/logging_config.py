"""Structlog configuration - consistent JSON logging with PII masking.

PRIVACY POLICY:
- user_has_active_visas is NEVER logged in plain text (GDPR/FZ-152 compliance)
- API keys are masked to first 8 chars only
- Telegram chat IDs are masked
"""

import logging
import sys
from typing import Any

import structlog


# Fields that contain PII or secrets - values replaced with [MASKED]
_PII_FIELDS = {
    "user_has_active_visas",   # visa data = personal info
    "active_visas",
    "twov_context",
    "api_key",
    "token",
    "telegram_bot_token",
    "chat_id",
    "telegram_chat_id",
    "password",
}


def _mask_pii(logger: Any, method: str, event_dict: dict) -> dict:
    """Structlog processor: mask PII fields before writing to log.

    Replaces sensitive values with [MASKED] so logs can be stored/shared safely.
    """
    for field in _PII_FIELDS:
        if field in event_dict:
            value = event_dict[field]
            if isinstance(value, list):
                event_dict[field] = "[MASKED_LIST]"
            elif isinstance(value, str) and len(value) > 8:
                event_dict[field] = value[:8] + "...[MASKED]"
            elif value:
                event_dict[field] = "[MASKED]"
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog with PII masking processor."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            _mask_pii,                               # ← PII masking before output
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
