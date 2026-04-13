"""Circuit Breaker pattern for scrapers.

Prevents cascading failures when a single scraper goes down.
States: CLOSED (normal) -> OPEN (failing) -> HALF_OPEN (testing recovery)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = structlog.get_logger()


class CircuitState(StrEnum):
    CLOSED = "closed"       # normal operation
    OPEN = "open"           # failing, reject fast
    HALF_OPEN = "half_open" # testing recovery


class CircuitBreaker:
    """Circuit breaker for a single scraper.

    fail_threshold: how many failures to trip the breaker
    recovery_timeout: seconds in OPEN state before trying HALF_OPEN
    """

    def __init__(
        self,
        name: str,
        fail_threshold: int = 3,
        recovery_timeout_secs: int = 3600,  # 1 hour
    ) -> None:
        self.name = name
        self.fail_threshold = fail_threshold
        self.recovery_timeout = timedelta(seconds=recovery_timeout_secs)

        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: datetime | None = None
        self._last_test_at: datetime | None = None

    @property
    def state(self) -> CircuitState:
        """Check if time to move from OPEN -> HALF_OPEN."""
        if self._state == CircuitState.OPEN and self._opened_at:
            elapsed = datetime.now(timezone.utc) - self._opened_at
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("circuit.half_open", scraper=self.name)
        return self._state

    @property
    def is_available(self) -> bool:
        return self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        """Reset on success - HALF_OPEN -> CLOSED."""
        if self._state == CircuitState.HALF_OPEN:
            logger.info("circuit.recovered", scraper=self.name)
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        """Count failure, trip breaker if threshold reached."""
        self._failures += 1
        logger.warning(
            "circuit.failure",
            scraper=self.name,
            failures=self._failures,
            threshold=self.fail_threshold,
        )

        if self._state == CircuitState.HALF_OPEN:
            # Failed during recovery test - back to OPEN
            self._state = CircuitState.OPEN
            self._opened_at = datetime.now(timezone.utc)
            logger.error("circuit.open_again", scraper=self.name)
        elif self._failures >= self.fail_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = datetime.now(timezone.utc)
            logger.error(
                "circuit.tripped",
                scraper=self.name,
                failures=self._failures,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scraper": self.name,
            "state": self.state.value,
            "failures": self._failures,
            "opened_at": self._opened_at.isoformat() if self._opened_at else None,
        }
