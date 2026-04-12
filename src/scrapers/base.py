"""Base scraper interface - all scrapers implement this contract."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.models.deals import DealCreate

logger = structlog.get_logger()


class BaseScraper(ABC):
    """SOLID: Interface Segregation - every scraper has one job: fetch deals."""

    def __init__(self, name: str, max_price_per_person: int = 400, adults: int = 2) -> None:
        self.name = name
        self.max_price_per_person = max_price_per_person
        self.adults = adults
        self._client: httpx.AsyncClient | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        before_sleep=lambda retry_state: logger.warning(
            "scraper.retry",
            attempt=retry_state.attempt_number,
            scraper=retry_state.args[0].name if retry_state.args else "unknown",
        ),
    )
    async def _fetch(self, url: str, params: dict | None = None) -> httpx.Response:
        """HTTP GET with retry logic."""
        client = await self.get_client()
        response = await client.get(url, params=params)
        response.raise_for_status()
        return response

    async def run(self) -> list[DealCreate]:
        """Execute scraper with timing and error handling."""
        start = time.monotonic()
        try:
            deals = await self.scrape()
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "scraper.completed",
                scraper=self.name,
                deals_found=len(deals),
                duration_ms=duration_ms,
            )
            return deals
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "scraper.failed",
                scraper=self.name,
                error=str(exc),
                duration_ms=duration_ms,
            )
            raise

    @abstractmethod
    async def scrape(self) -> list[DealCreate]:
        """Implement in subclass - fetch and parse deals."""
        ...
