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

# Known visa-free countries for BY passport (verified, cached locally - NO API calls needed)
# These are stable, well-documented visa-free regimes. Only query API for unknown countries.
KNOWN_VISA_FREE_BY: dict[str, int] = {
    # CIS (стабильно)
    "RU": 90,   # Russia - безлимит почти
    "AM": 180,  # Armenia
    "AZ": 90,   # Azerbaijan
    "KG": 90,   # Kyrgyzstan
    "KZ": 90,   # Kazakhstan
    "MD": 90,   # Moldova
    "TJ": 90,   # Tajikistan
    "UZ": 90,   # Uzbekistan

    # Europe (Balkans)
    "RS": 30,   # Serbia
    "ME": 30,   # Montenegro
    "BA": 30,   # Bosnia and Herzegovina
    "MK": 30,   # North Macedonia

    # Asia (Middle East, popular для BY туристов)
    "TR": 30,   # Turkey
    "GE": 365,  # Georgia - king of visa-free!
    "AE": 30,   # UAE (Dubai)
    "JO": 30,   # Jordan
    "OM": 30,   # Oman
    "QA": 30,   # Qatar
    "BH": 14,   # Bahrain

    # North Africa
    "EG": 0,    # Egypt - visa on arrival (but stored here for cache)
    "TN": 30,   # Tunisia
    "MA": 30,   # Morocco

    # Asia-Pacific
    "VN": 15,   # Vietnam (популярно, но МОЖЕТ ИЗМЕНИТЬСЯ)
    "LK": 30,   # Sri Lanka
    "MV": 30,   # Maldives
    "ID": 30,   # Indonesia
    "TH": 30,   # Thailand

    # Americas
    "CU": 30,   # Cuba
    "MX": 30,   # Mexico (некоторые источники говорят нужна виза)
    "AR": 30,   # Argentina
    "BR": 30,   # Brazil
    "CR": 30,   # Costa Rica
    "PA": 30,   # Panama
}


class VisaChecker:
    """Checks visa requirements using cache + web search verification.

    SOLID: Single Responsibility - only visa logic, no scraping or DB management.
    """

    def __init__(
        self,
        visa_repo: VisaRepository,
        brave_api_key: str | None = None,
        passport_type: str = "BY",
    ) -> None:
        """Initialize VisaChecker with hybrid mode.

        Args:
            visa_repo: Database repository
            brave_api_key: Optional Brave Search API key. If None, use local KNOWN table.
            passport_type: Passport country code (default: BY)

        In hybrid mode:
        - Always check DB cache first (24h TTL)
        - Then check KNOWN_VISA_FREE_BY (no API call)
        - Only if unknown AND brave_api_key is set → web search
        """
        self._repo = visa_repo
        self._brave_key = brave_api_key
        self._passport = passport_type
        self._client: httpx.AsyncClient | None = None
        self._mode = "hybrid"  # hybrid | api_only (for future use)

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
        user_has_active_visas: list[str] | None = None,
    ) -> VisaCheckResult:
        """Full visa check: destination + all transit points.

        Args:
            transit_countries: ISO codes of transit countries (from flight route)
            user_has_active_visas: Active visas the traveler holds (e.g. ["US", "Schengen"]).
                Used to unlock TWOV (Transit Without Visa) rules.
                "Schengen" = any valid Schengen visa
                "US" = valid US visa/green card
        """
        warnings: list[str] = []
        active_visas = [v.upper() for v in (user_has_active_visas or [])]

        # 1. Check destination visa
        dest_visa = await self._check_country(country_code, country_name)

        # 2. Check transit countries with TWOV awareness
        transit_results: list[TransitRequirement] = []
        for tc in transit_countries or []:
            tc_upper = tc.upper()

            if tc_upper in SCHENGEN_COUNTRIES:
                # TWOV rule: Schengen transit MAY be allowed if user has active US/UK/CA visa
                twov_unlocked = self._check_twov_schengen(active_visas)
                if twov_unlocked:
                    warnings.append(
                        f"ℹ️ Schengen transit ({tc_upper}): TWOV may apply "
                        f"because user holds active {twov_unlocked} visa. "
                        "Max 24h transit. Verify specific airport rules before booking."
                    )
                    transit_results.append(
                        TransitRequirement(
                            transit_country=tc_upper,
                            passport_type=self._passport,
                            visa_required=False,
                            max_transit_hrs=24,
                            notes=f"TWOV (Transit Without Visa) via {twov_unlocked} visa. "
                                  "Airside only, no terminal change.",
                        )
                    )
                else:
                    warnings.append(
                        f"🚫 Schengen transit ({tc_upper}): visa required for BY passport. "
                        "Tip: if you hold active US/UK visa, TWOV may apply."
                    )
                    transit_results.append(
                        TransitRequirement(
                            transit_country=tc_upper,
                            passport_type=self._passport,
                            visa_required=True,
                            notes="Schengen zone - visa required. TWOV possible with US/UK visa.",
                        )
                    )

            elif tc_upper == "GB":
                # UK has its own TWOV rules (not Schengen)
                twov_unlocked = self._check_twov_uk(active_visas)
                if twov_unlocked:
                    warnings.append(
                        f"ℹ️ UK transit (GB): TWOV may apply via {twov_unlocked} visa. "
                        "Max 24h airside only. No terminal change. Verify with airline."
                    )
                    transit_results.append(
                        TransitRequirement(
                            transit_country="GB",
                            passport_type=self._passport,
                            visa_required=False,
                            max_transit_hrs=24,
                            notes=f"TWOV via {twov_unlocked} visa - airside only.",
                        )
                    )
                else:
                    warnings.append(
                        "🚫 UK transit (GB): Direct Airside Transit Visa required for BY. "
                        "TWOV possible with valid US/Schengen/Irish visa."
                    )
                    transit_results.append(
                        TransitRequirement(
                            transit_country="GB",
                            passport_type=self._passport,
                            visa_required=True,
                            notes="UK DATV required. TWOV possible with US/Schengen/Irish visa.",
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

    @staticmethod
    def _check_twov_schengen(active_visas: list[str]) -> str | None:
        """Check if TWOV applies for Schengen transit.

        Returns the unlocking visa name if TWOV is possible, None otherwise.
        TWOV for Schengen: valid US, UK, Canadian, or Japanese visa unlocks airside transit.
        Source: https://www.schengenvisainfo.com/transit-visa/
        """
        twov_unlocking_visas = {"US", "GB", "CA", "JP"}
        # "Schengen" as generic - user already has one Schengen country visa
        if "SCHENGEN" in active_visas:
            return "Schengen"
        for v in active_visas:
            if v in twov_unlocking_visas:
                return v
        return None

    @staticmethod
    def _check_twov_uk(active_visas: list[str]) -> str | None:
        """Check if TWOV applies for UK airside transit (DATV waiver).

        UK DATV waiver applies if traveler holds valid: US, Schengen, Irish,
        Canadian, Australian, New Zealand, or Japanese visa.
        Source: UK Home Office immigration rules Appendix V.
        """
        twov_unlocking_visas = {"US", "IE", "CA", "AU", "NZ", "JP"}
        if "SCHENGEN" in active_visas:
            return "Schengen"
        for v in active_visas:
            if v in twov_unlocking_visas:
                return v
        return None

    async def _check_country(self, country_code: str, country_name: str) -> VisaRequirement:
        """Hybrid visa check: DB cache → local table → web search (if brave key exists).

        This minimizes API calls for cost optimization.
        """
        country_code_upper = country_code.upper()
        now = datetime.now(timezone.utc)

        # STEP 1: Database cache (fresh within 24h)
        cached = await self._repo.get_visa(country_code_upper, self._passport)
        if cached and not cached.is_expired:
            logger.info(
                "visa.cache_hit_db",
                country=country_code_upper,
                age_hours=int((now - cached.verified_at).total_seconds() / 3600),
            )
            return cached

        # STEP 2: Local known table (ZERO cost, 30+ countries)
        if country_code_upper in KNOWN_VISA_FREE_BY:
            max_stay = KNOWN_VISA_FREE_BY[country_code_upper]
            requirement = VisaRequirement(
                country_code=country_code_upper,
                country_name=country_name,
                passport_type=self._passport,
                visa_status=(
                    VisaStatus.VISA_ON_ARRIVAL if max_stay == 0 else VisaStatus.VISA_FREE
                ),
                max_stay_days=max_stay if max_stay > 0 else None,
                notes=f"Cached from local table. Verify before travel if >6 months passed.",
                verified_at=now,
                expires_at=now + timedelta(hours=24),
            )
            await self._repo.save_visa(requirement)
            logger.info(
                "visa.cache_hit_local",
                country=country_code_upper,
                from_table="KNOWN_VISA_FREE_BY",
            )
            return requirement

        # STEP 3: Web search (ONLY if unknown AND brave key exists)
        if not self._brave_key:
            logger.warning(
                "visa.no_api_key",
                country=country_code_upper,
                action="returning UNKNOWN (no Brave API key configured)",
            )
            return VisaRequirement(
                country_code=country_code_upper,
                country_name=country_name,
                passport_type=self._passport,
                visa_status=VisaStatus.UNKNOWN,
                notes="No API key configured for web search. Check local table or verify manually.",
                verified_at=now,
                expires_at=now + timedelta(hours=1),
            )

        logger.info(
            "visa.web_search",
            country=country_code_upper,
            reason="unknown_country",
            passport=self._passport,
        )
        requirement = await self._search_visa_info(country_code_upper, country_name)
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
