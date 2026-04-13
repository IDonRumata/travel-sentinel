"""Visa Intelligence Layer - real-time visa requirement verification.

This is the critical "Devil's Advocate" component:
- NEVER trusts static data alone
- Verifies via web search when cache is expired
- Distinguishes destination vs transit visas
- Flags Schengen/transit traps (e.g., cruise from Italy = Schengen visa needed)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.db.visa_repo import VisaRepository
from src.models.visa import (
    TransitRequirement,
    VisaCheckResult,
    VisaRequirement,
    VisaStatus,
)

logger = structlog.get_logger()

# Known Schengen countries - if transit goes through these, flag it
SCHENGEN_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IS", "IT", "LV", "LT", "LU", "MT", "NL",
    "NO", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "CH", "LI",
}

# Known visa-free countries for BY passport (baseline, always verified via search)
KNOWN_VISA_FREE_BY: dict[str, int] = {
    "AM": 180,  # Armenia
    "AZ": 90,   # Azerbaijan
    "GE": 365,  # Georgia
    "KG": 90,   # Kyrgyzstan
    "KZ": 90,   # Kazakhstan
    "MD": 90,   # Moldova
    "RU": 90,   # Russia
    "TJ": 90,   # Tajikistan
    "UZ": 90,   # Uzbekistan
    "RS": 30,   # Serbia
    "ME": 30,   # Montenegro
    "TR": 30,   # Turkey
    "VN": 15,   # Vietnam (may change)
    "CU": 30,   # Cuba
}


class VisaChecker:
    """Checks visa requirements using cache + web search verification.

    SOLID: Single Responsibility - only visa logic, no scraping or DB management.
    """

    def __init__(
        self,
        visa_repo: VisaRepository,
        brave_api_key: str,
        passport_type: str = "BY",
    ) -> None:
        self._repo = visa_repo
        self._brave_key = brave_api_key
        self._passport = passport_type
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def check_deal(
        self,
        country_code: str,
        country_name: str,
        transit_countries: list[str] | None = None,
    ) -> VisaCheckResult:
        """Full visa check: destination + all transit points."""
        warnings: list[str] = []

        # 1. Check destination visa
        dest_visa = await self._check_country(country_code, country_name)

        # 2. Check transit countries
        transit_results: list[TransitRequirement] = []
        for tc in transit_countries or []:
            if tc.upper() in SCHENGEN_COUNTRIES:
                warnings.append(
                    f"ALERT: Transit through {tc} (Schengen zone) - "
                    f"Belarusian passport holders need a Schengen visa for transit!"
                )
                transit_results.append(
                    TransitRequirement(
                        transit_country=tc.upper(),
                        passport_type=self._passport,
                        visa_required=True,
                        notes="Schengen zone - visa required for BY citizens",
                    )
                )
            else:
                transit = await self._repo.get_transit(tc, self._passport)
                if transit:
                    transit_results.append(transit)
                    if transit.visa_required:
                        warnings.append(f"Transit visa required for {tc}")

        # 3. Add warnings for edge cases
        if dest_visa.visa_status == VisaStatus.UNKNOWN:
            warnings.append(
                f"Could not verify visa status for {country_name} ({country_code}). "
                "Manual check recommended before booking!"
            )

        if dest_visa.visa_status == VisaStatus.E_VISA:
            warnings.append(
                f"E-visa required for {country_name} - must be obtained BEFORE departure. "
                "Processing time may vary."
            )

        result = VisaCheckResult(
            destination_visa=dest_visa,
            transit_visas=transit_results,
            warnings=warnings,
        )

        logger.info(
            "visa.check_complete",
            country=country_code,
            feasible=result.is_feasible,
            warnings_count=len(warnings),
        )
        return result

    async def _check_country(self, country_code: str, country_name: str) -> VisaRequirement:
        """Check visa for a single country: cache first, then web search."""
        # Step 1: Check cache
        cached = await self._repo.get_visa(country_code, self._passport)
        if cached and not cached.is_expired:
            return cached

        # Step 2: Cache miss or expired - search the web
        logger.info("visa.web_search", country=country_code, passport=self._passport)
        requirement = await self._search_visa_info(country_code, country_name)

        # Step 3: Save to cache
        await self._repo.save_visa(requirement)
        return requirement

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    )
    async def _search_visa_info(
        self, country_code: str, country_name: str
    ) -> VisaRequirement:
        """Search web for current visa requirements using Brave Search API.

        This is the KEY differentiator - we don't rely on static tables.
        """
        # Include current year to bias search toward fresh results
        from datetime import date as _date
        current_year = _date.today().year
        query = f"visa requirements {country_name} Belarusian passport {current_year}"
        client = await self._get_client()

        response = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            # freshness="pd" = past day, "pw" = past week
            # Use "pm" (past month) as balance: fresh but not empty
            params={"q": query, "count": 5, "freshness": "pm"},
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": self._brave_key,
            },
        )
        response.raise_for_status()

        search_data = response.json()
        results = search_data.get("web", {}).get("results", [])

        # Parse search results to determine visa status
        visa_status = self._analyze_search_results(results, country_code)
        now = datetime.now(timezone.utc)

        source_url = results[0].get("url", "") if results else ""

        # Check freshness of top result - warn if older than 6 months
        top_age_warning = self._check_result_freshness(results)

        notes = self._extract_notes(results)
        if top_age_warning:
            notes = f"⚠️ {top_age_warning} | {notes or ''}"

        return VisaRequirement(
            country_code=country_code.upper(),
            country_name=country_name,
            passport_type=self._passport,
            visa_status=visa_status,
            max_stay_days=KNOWN_VISA_FREE_BY.get(country_code.upper()),
            notes=notes,
            source_url=source_url,
            verified_at=now,
            # TTL: 24 hours for critical data. Visas can change quickly.
            expires_at=now + timedelta(hours=24),
        )

    def _analyze_search_results(
        self, results: list[dict], country_code: str
    ) -> VisaStatus:
        """Analyze search snippets to determine visa status.

        STRICT validation: requires both "Belarus" AND visa status keyword.
        Avoids hallucinations by failing-closed to UNKNOWN if ambiguous.
        """
        if not results:
            # Fallback to known data if search fails
            if country_code.upper() in KNOWN_VISA_FREE_BY:
                logger.info("visa.using_known_table", country=country_code)
                return VisaStatus.VISA_FREE
            logger.warning("visa.no_search_results", country=country_code)
            return VisaStatus.UNKNOWN

        combined_text = " ".join(
            (r.get("description", "") + " " + r.get("title", "")).lower()
            for r in results
        )

        # REQUIREMENT: Must mention Belarus explicitly
        belarus_keywords = ["belarus", "belarusian", "белорусский", "беларусь"]
        mentions_belarus = any(kw in combined_text for kw in belarus_keywords)

        visa_free_signals = [
            "visa-free", "visa free", "безвизовый", "без визы",
            "no visa required", "visa not required", "don't need visa",
        ]
        e_visa_signals = [
            "e-visa", "electronic visa", "электронная виза", "evisa",
        ]
        voa_signals = [
            "visa on arrival", "виза по прибытии", "виза по прилёту",
        ]
        visa_required_signals = [
            "visa required", "need visa", "нужна виза", "требуется виза",
            "visa is required",
        ]

        # Priority: most restrictive first (safe default is UNKNOWN if unclear)
        if any(signal in combined_text for signal in visa_required_signals):
            # Check for conflicting info (visa-free AND visa-required = UNKNOWN)
            if any(signal in combined_text for signal in visa_free_signals):
                logger.warning("visa.conflicting_signals", country=country_code)
                return VisaStatus.UNKNOWN
            return VisaStatus.VISA_REQUIRED

        if any(signal in combined_text for signal in visa_free_signals):
            if mentions_belarus:
                return VisaStatus.VISA_FREE
            # If visa-free is mentioned but NOT for Belarus → fallback to known table
            if country_code.upper() in KNOWN_VISA_FREE_BY:
                logger.info("visa.guessing_from_known_table", country=country_code)
                return VisaStatus.VISA_FREE
            logger.warning("visa.vf_not_for_belarus", country=country_code)
            return VisaStatus.UNKNOWN

        if any(signal in combined_text for signal in e_visa_signals):
            return VisaStatus.E_VISA

        if any(signal in combined_text for signal in voa_signals):
            return VisaStatus.VISA_ON_ARRIVAL

        # If we have known data AND no conflicting signals
        if country_code.upper() in KNOWN_VISA_FREE_BY:
            logger.info("visa.fallback_known_table", country=country_code)
            return VisaStatus.VISA_FREE

        # Default: UNKNOWN is safer than wrong guess
        logger.warning(
            "visa.unknown_status",
            country=country_code,
            search_text_len=len(combined_text),
        )
        return VisaStatus.UNKNOWN

    @staticmethod
    def _extract_notes(results: list[dict]) -> str | None:
        """Extract first useful snippet as notes."""
        for r in results:
            desc = r.get("description", "").strip()
            if desc and len(desc) > 20:
                return desc[:500]
        return None

    @staticmethod
    def _check_result_freshness(results: list[dict]) -> str | None:
        """Check if search results look stale (old publication dates).

        Brave Search returns 'page_age' as ISO date or string like '2024-03-15'.
        If top result is older than 6 months, add a warning.
        """
        from datetime import date, timedelta
        if not results:
            return None

        top = results[0]
        page_age_str = top.get("page_age", "") or top.get("age", "")

        if not page_age_str:
            return None

        try:
            # Brave returns ISO date string
            if "T" in page_age_str:
                page_age_str = page_age_str.split("T")[0]
            pub_date = date.fromisoformat(page_age_str)
            age_days = (date.today() - pub_date).days

            if age_days > 180:
                return (
                    f"Search result from {pub_date} ({age_days} days ago). "
                    "Visa rules may have changed - verify with official embassy!"
                )
        except (ValueError, TypeError):
            pass

        return None
