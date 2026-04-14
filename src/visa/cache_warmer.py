"""Proactive visa cache warmer - runs weekly to detect policy changes.

Purpose: Prevent "stale data" incidents (e.g., Vietnam suddenly requiring visa).
Strategy:
- Ignores cache TTL - ALWAYS re-fetches from web for watched countries
- Compares new status with cached status
- If STATUS CHANGED → immediate Telegram alert before any user sees stale data
- Designed to run as a weekly cron in n8n

Cost estimate: 10 countries × $0.02/search = $0.20/week = $0.87/month (acceptable)
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog

from src.db.visa_repo import VisaRepository
from src.models.visa import VisaRequirement, VisaStatus

logger = structlog.get_logger()

# Countries that frequently change visa policy for BY citizens
# Sorted by: popularity + historical volatility
TOP_WATCHED_COUNTRIES: list[tuple[str, str]] = [
    ("TR", "Turkey"),        # Most popular, historically stable but watch
    ("EG", "Egypt"),         # Very popular, stable VOA
    ("TH", "Thailand"),      # Changed rules multiple times
    ("AE", "UAE"),           # Popular, watch sanctions impact
    ("LK", "Sri Lanka"),     # Changed from e-visa to VOA recently
    ("VN", "Vietnam"),       # 15-day limit, volatile
    ("ID", "Indonesia"),     # Bali popular, rules change
    ("GE", "Georgia"),       # Extremely popular for BY, watch carefully
    ("RS", "Serbia"),        # EU candidate, rules may tighten
    ("ME", "Montenegro"),    # Seasonal rules
]


class VisaCacheWarmer:
    """Weekly background service to detect visa policy changes.

    This is the "canary in the coal mine" - catches changes before users see stale data.
    """

    def __init__(
        self,
        visa_repo: VisaRepository,
        brave_api_key: str,
        telegram_bot_token: str,
        telegram_chat_id: str,
        passport_type: str = "BY",
    ) -> None:
        self._repo = visa_repo
        self._brave_key = brave_api_key
        self._telegram_token = telegram_bot_token
        self._telegram_chat_id = telegram_chat_id
        self._passport = passport_type
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def warm_all(self) -> dict:
        """Re-check all watched countries, alert on status changes.

        Returns summary dict suitable for n8n HTTP response.
        """
        now = datetime.now(timezone.utc)
        results = []
        changes_detected: list[dict] = []

        logger.info("cache_warmer.start", countries=len(TOP_WATCHED_COUNTRIES))

        for country_code, country_name in TOP_WATCHED_COUNTRIES:
            try:
                result = await self._warm_country(country_code, country_name)
                results.append(result)

                if result.get("status_changed"):
                    changes_detected.append(result)
                    logger.warning(
                        "cache_warmer.status_changed",
                        country=country_code,
                        old=result["old_status"],
                        new=result["new_status"],
                    )

            except Exception as exc:
                logger.error(
                    "cache_warmer.country_error",
                    country=country_code,
                    error=str(exc),
                )
                results.append({
                    "country": country_code,
                    "status": "error",
                    "error": str(exc),
                })

        # Send Telegram alert if any status changed
        if changes_detected:
            await self._send_change_alert(changes_detected)

        summary = {
            "checked": len(TOP_WATCHED_COUNTRIES),
            "changes_detected": len(changes_detected),
            "errors": sum(1 for r in results if r.get("status") == "error"),
            "timestamp": now.isoformat(),
            "results": results,
        }

        logger.info(
            "cache_warmer.complete",
            checked=summary["checked"],
            changes=summary["changes_detected"],
        )
        return summary

    async def _warm_country(self, country_code: str, country_name: str) -> dict:
        """Force-refresh one country, bypassing cache TTL."""
        # Get current cached status BEFORE refreshing
        old_req = await self._repo.get_visa(country_code, self._passport)
        old_status = old_req.visa_status.value if old_req else "not_cached"

        # Force web search regardless of cache TTL
        new_req = await self._search_current_status(country_code, country_name)

        # Compare old vs new
        status_changed = (
            old_req is not None
            and old_req.visa_status != new_req.visa_status
            and old_req.visa_status != VisaStatus.UNKNOWN
            and new_req.visa_status != VisaStatus.UNKNOWN
        )

        # Save fresh data
        await self._repo.save_visa(new_req)

        return {
            "country": country_code,
            "country_name": country_name,
            "old_status": old_status,
            "new_status": new_req.visa_status.value,
            "status_changed": status_changed,
            "source_url": new_req.source_url or "",
        }

    async def _search_current_status(
        self, country_code: str, country_name: str
    ) -> VisaRequirement:
        """Search Brave for CURRENT visa status (force-fresh, no cache)."""
        from datetime import date, timedelta

        year = date.today().year
        query = f"Belarus visa {country_name} requirements {year} update"
        client = await self._get_client()

        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": 5, "freshness": "pw"},  # past week
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self._brave_key,
            },
        )
        response.raise_for_status()

        data = response.json()
        results = data.get("web", {}).get("results", [])

        # Reuse analysis logic from VisaChecker
        visa_status = self._analyze(results, country_code)
        now = datetime.now(timezone.utc)

        return VisaRequirement(
            country_code=country_code.upper(),
            country_name=country_name,
            passport_type=self._passport,
            visa_status=visa_status,
            notes=self._extract_snippet(results),
            source_url=results[0].get("url", "") if results else None,
            verified_at=now,
            expires_at=now + timedelta(hours=24),
        )

    def _analyze(self, results: list[dict], country_code: str) -> VisaStatus:
        """Keyword analysis - same logic as VisaChecker._analyze_search_results."""
        if not results:
            return VisaStatus.UNKNOWN

        combined = " ".join(
            (r.get("description", "") + " " + r.get("title", "")).lower()
            for r in results
        )

        if any(s in combined for s in ["visa required", "need visa", "нужна виза"]):
            if any(s in combined for s in ["visa-free", "visa free", "без визы"]):
                return VisaStatus.UNKNOWN  # conflicting
            return VisaStatus.VISA_REQUIRED

        if any(s in combined for s in ["visa-free", "visa free", "без визы", "no visa"]):
            return VisaStatus.VISA_FREE

        if any(s in combined for s in ["e-visa", "electronic visa", "evisa"]):
            return VisaStatus.E_VISA

        if any(s in combined for s in ["visa on arrival", "по прибытии"]):
            return VisaStatus.VISA_ON_ARRIVAL

        return VisaStatus.UNKNOWN

    @staticmethod
    def _extract_snippet(results: list[dict]) -> str | None:
        for r in results:
            desc = r.get("description", "").strip()
            if desc and len(desc) > 20:
                return desc[:500]
        return None

    async def _send_change_alert(self, changes: list[dict]) -> None:
        """Send urgent Telegram alert when visa policy changes detected."""
        lines = ["<b>🚨 Travel Sentinel: Изменение визовых правил!</b>\n"]

        for ch in changes:
            old = ch["old_status"]
            new = ch["new_status"]
            country = ch["country_name"]

            # Determine severity
            if new == "visa_required" and old in ("visa_free", "visa_on_arrival"):
                icon = "🔴"
                msg = f"<b>СТОП: {country} ввела визовый режим!</b>\nБыло: {old} → Стало: {new}"
            elif new == "visa_free" and old == "visa_required":
                icon = "🟢"
                msg = f"<b>ХОРОШИЕ НОВОСТИ: {country} открыла безвиз!</b>\nБыло: {old} → Стало: {new}"
            else:
                icon = "🟡"
                msg = f"{country}: изменение режима\nБыло: {old} → Стало: {new}"

            lines.append(f"{icon} {msg}")
            if ch.get("source_url"):
                lines.append(f'Источник: {ch["source_url"]}\n')

        lines.append("\n<i>Проверь актуальность всех туров в БД!</i>")

        try:
            client = await self._get_client()
            await client.post(
                f"https://api.telegram.org/bot{self._telegram_token}/sendMessage",
                json={
                    "chat_id": self._telegram_chat_id,
                    "text": "\n".join(lines),
                    "parse_mode": "HTML",
                },
            )
            logger.info("cache_warmer.alert_sent", changes=len(changes))
        except Exception as exc:
            logger.error("cache_warmer.alert_failed", error=str(exc))
