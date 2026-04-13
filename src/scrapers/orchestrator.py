"""Scraper Orchestrator - manages fallbacks, circuit breakers, and alerts.

The "brain" of the scraping layer. Handles graceful degradation:
1. Try all scrapers
2. If one fails -> circuit breaker trips -> skip that scraper
3. If ALL scrapers fail -> serve cached data from DB
4. Send Telegram alert with specific failure reason
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog

from src.db.deals_repo import DealsRepository
from src.models.config import Settings
from src.models.deals import DealCreate, Deal
from src.scrapers.aviasales import AviasalesScraper
from src.scrapers.base import BaseScraper
from src.scrapers.circuit_breaker import CircuitBreaker, CircuitState
from src.scrapers.travelata import TravelataScraper

logger = structlog.get_logger()

# HTTP errors that trip the circuit breaker (bot protection, bans)
FATAL_STATUS_CODES = {403, 429, 503}

# HTTP errors that are transient (retry is OK)
TRANSIENT_STATUS_CODES = {500, 502, 504}


class ScraperOrchestrator:
    """Manages all scrapers with circuit breaker and graceful degradation."""

    def __init__(self, settings: Settings, deals_repo: DealsRepository) -> None:
        self._settings = settings
        self._repo = deals_repo

        # Circuit breakers - one per scraper
        self._breakers: dict[str, CircuitBreaker] = {
            "aviasales": CircuitBreaker(
                name="aviasales",
                fail_threshold=3,
                recovery_timeout_secs=1800,  # 30 min - API should recover fast
            ),
            "travelata": CircuitBreaker(
                name="travelata",
                fail_threshold=3,
                recovery_timeout_secs=3600,  # 1 hour - web scraping ban recovery
            ),
        }

        self._telegram_client: httpx.AsyncClient | None = None

    async def _get_telegram_client(self) -> httpx.AsyncClient:
        if self._telegram_client is None or self._telegram_client.is_closed:
            self._telegram_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        return self._telegram_client

    async def run_all(self) -> dict[str, Any]:
        """Run all available scrapers with circuit breaker protection.

        Returns summary with deals found, failures, and operational mode.
        """
        start = time.monotonic()
        all_deals: list[DealCreate] = []
        skipped_scrapers: list[str] = []
        failed_scrapers: list[dict] = []

        scrapers: list[tuple[str, BaseScraper]] = [
            (
                "aviasales",
                AviasalesScraper(
                    api_token=self._settings.aviasales_token,
                    max_price_per_person=self._settings.max_price_per_person_usd,
                    adults=self._settings.adults,
                ),
            ),
            (
                "travelata",
                TravelataScraper(
                    max_price_per_person=self._settings.max_price_per_person_usd,
                    adults=self._settings.adults,
                ),
            ),
        ]

        for name, scraper in scrapers:
            breaker = self._breakers[name]

            # Circuit is OPEN - skip fast, no HTTP call
            if not breaker.is_available:
                logger.warning(
                    "orchestrator.scraper_skipped",
                    scraper=name,
                    reason="circuit_open",
                    breaker=breaker.to_dict(),
                )
                skipped_scrapers.append(name)
                continue

            try:
                deals = await scraper.run()
                breaker.record_success()
                all_deals.extend(deals)

                # Log to scrape_log table
                await self._repo.log_scrape(
                    scraper_name=name,
                    status="success",
                    deals_found=len(deals),
                    deals_new=0,  # updated below after upsert
                    duration_ms=0,
                )

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code

                if status_code in FATAL_STATUS_CODES:
                    breaker.record_failure()
                    error_msg = f"HTTP {status_code} - Bot protection or ban"

                    if status_code == 403:
                        error_msg = "HTTP 403 Forbidden - IP or User-Agent banned"
                    elif status_code == 429:
                        error_msg = "HTTP 429 Too Many Requests - Rate limited"

                    logger.error(
                        "orchestrator.scraper_banned",
                        scraper=name,
                        status=status_code,
                        circuit_state=breaker.state.value,
                    )
                    failed_scrapers.append({"scraper": name, "error": error_msg, "code": status_code})

                    await self._repo.log_scrape(
                        scraper_name=name,
                        status="error",
                        deals_found=0,
                        deals_new=0,
                        duration_ms=0,
                        error_message=error_msg,
                    )

                else:
                    # Transient error - don't trip circuit immediately
                    logger.warning(
                        "orchestrator.transient_error",
                        scraper=name,
                        status=status_code,
                    )
                    failed_scrapers.append({"scraper": name, "error": f"HTTP {status_code}", "code": status_code})

            except Exception as exc:
                breaker.record_failure()
                logger.error("orchestrator.scraper_error", scraper=name, error=str(exc))
                failed_scrapers.append({"scraper": name, "error": str(exc), "code": 0})

            finally:
                await scraper.close()

        # Persist all collected deals to DB
        new_count = 0
        for deal in all_deals:
            try:
                _, is_new = await self._repo.upsert_deal(deal)
                if is_new:
                    new_count += 1
            except Exception as exc:
                logger.error("orchestrator.upsert_error", error=str(exc))

        duration_ms = int((time.monotonic() - start) * 1000)
        has_live_data = len(all_deals) > 0
        open_circuits = [
            name
            for name, br in self._breakers.items()
            if br.state == CircuitState.OPEN
        ]

        # Determine operational mode
        if has_live_data and not failed_scrapers:
            mode = "full"
        elif has_live_data and failed_scrapers:
            mode = "partial"
        else:
            mode = "cache_only"

        summary = {
            "mode": mode,
            "deals_found": len(all_deals),
            "deals_new": new_count,
            "duration_ms": duration_ms,
            "failed": failed_scrapers,
            "skipped": skipped_scrapers,
            "open_circuits": open_circuits,
        }

        # Decide whether to send Telegram alert
        await self._maybe_alert(mode, failed_scrapers, open_circuits, all_deals)

        return summary

    async def get_fallback_deals(self, limit: int = 20) -> list[Deal]:
        """When ALL scrapers fail, serve cached deals from DB.

        Better than empty response - stale data is still useful.
        """
        logger.warning("orchestrator.serving_cache", reason="all scrapers failed")
        return await self._repo.get_cheapest(limit=limit)

    def get_circuit_status(self) -> list[dict]:
        """Health status of all scrapers for /health endpoint."""
        return [br.to_dict() for br in self._breakers.values()]

    async def _maybe_alert(
        self,
        mode: str,
        failed_scrapers: list[dict],
        open_circuits: list[str],
        deals: list[DealCreate],
    ) -> None:
        """Send Telegram alert if something went wrong.

        Human-in-loop: alert only when there's something actionable.
        """
        # No alert needed if everything is fine
        if mode == "full" and not open_circuits:
            return

        message_lines: list[str] = []

        if mode == "cache_only":
            message_lines.append("<b>⚠️ Travel Sentinel: все скрейперы упали</b>")
            message_lines.append("Работаю только с кешем БД. Новые цены не проверяю.")
        elif mode == "partial":
            message_lines.append("<b>⚡ Travel Sentinel: частичный отказ</b>")

        for fail in failed_scrapers:
            code = fail.get("code", 0)
            name = fail["scraper"]

            if code == 403:
                message_lines.append(
                    f"- <b>{name}</b>: 403 Forbidden. Нас забанили по IP или User-Agent. "
                    "Нужна смена прокси или User-Agent."
                )
            elif code == 429:
                message_lines.append(
                    f"- <b>{name}</b>: 429 Rate Limited. Жду {self._breakers[name].recovery_timeout.total_seconds()//60:.0f} мин."
                )
            else:
                message_lines.append(f"- <b>{name}</b>: {fail['error']}")

        if open_circuits:
            message_lines.append(
                f"\n🔴 Circuit Breaker OPEN: {', '.join(open_circuits)}"
            )
            message_lines.append("Автоматически попробую восстановиться через час.")

        if not message_lines:
            return

        message = "\n".join(message_lines)

        try:
            client = await self._get_telegram_client()
            await client.post(
                f"https://api.telegram.org/bot{self._settings.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": self._settings.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                },
            )
            logger.info("orchestrator.alert_sent", mode=mode)
        except Exception as exc:
            # Alert failure should never crash the orchestrator
            logger.error("orchestrator.alert_failed", error=str(exc))

    async def close(self) -> None:
        if self._telegram_client and not self._telegram_client.is_closed:
            await self._telegram_client.aclose()
