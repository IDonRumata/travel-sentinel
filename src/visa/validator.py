"""Deterministic validators for VisaChecker output quality.

Level 1 of the Quality Pyramid: FREE, runs on EVERY response.
Catches obvious failures without spending a single token.
"""

from __future__ import annotations

import re

import structlog

from src.models.visa import VisaCheckResult, VisaRequirement, VisaStatus

logger = structlog.get_logger()

# Valid ISO 3166-1 alpha-2 codes (subset of real countries we care about)
VALID_COUNTRY_CODES = {
    "TR", "EG", "TH", "AE", "GE", "AM", "LK", "MV", "CU", "RS",
    "ME", "TN", "VN", "AZ", "UZ", "KG", "KZ", "TJ", "MD", "RU",
    "ID", "MY", "MX", "BR", "AR", "MA", "JO", "OM", "QA", "BH",
}

# Suspicious patterns that indicate hallucination
HALLUCINATION_PATTERNS = [
    r"as an ai",
    r"i cannot",
    r"i don'?t have",
    r"please note that",
    r"it'?s important to",
]


class VisaResultValidator:
    """Validates VisaChecker output for structural and logical correctness.

    This is NOT an LLM call. Pure deterministic checks.
    Think of it as a schema validator for AI output.
    """

    def validate(self, result: VisaCheckResult) -> list[str]:
        """Run all validators, return list of issues found."""
        issues: list[str] = []

        issues.extend(self._check_country_code(result.destination_visa))
        issues.extend(self._check_visa_status_logic(result.destination_visa))
        issues.extend(self._check_stay_days(result.destination_visa))
        issues.extend(self._check_notes_quality(result.destination_visa))
        issues.extend(self._check_transit_consistency(result))

        if issues:
            logger.warning(
                "validator.issues_found",
                country=result.destination_visa.country_code,
                issues=issues,
                count=len(issues),
            )
        else:
            logger.info(
                "validator.passed",
                country=result.destination_visa.country_code,
            )

        return issues

    def _check_country_code(self, visa: VisaRequirement) -> list[str]:
        """Is the country code valid ISO 3166-1 alpha-2?"""
        issues = []
        code = visa.country_code.upper()

        if len(code) != 2:
            issues.append(f"Invalid country code length: '{code}'")

        if not code.isalpha():
            issues.append(f"Country code contains non-alpha chars: '{code}'")

        return issues

    def _check_visa_status_logic(self, visa: VisaRequirement) -> list[str]:
        """Logical consistency of visa status + stay days."""
        issues = []

        # If visa-free, should have max_stay_days
        if visa.visa_status == VisaStatus.VISA_FREE and visa.max_stay_days is None:
            issues.append(
                f"visa_free for {visa.country_code} but max_stay_days is None. "
                "Every visa-free entry has a limit."
            )

        # If visa required, stay_days should be None (you can't stay without visa)
        if visa.visa_status == VisaStatus.VISA_REQUIRED and visa.max_stay_days is not None:
            issues.append(
                f"visa_required for {visa.country_code} but max_stay_days={visa.max_stay_days}. "
                "This is contradictory."
            )

        # Sanity check on stay days
        if visa.max_stay_days is not None:
            if visa.max_stay_days <= 0:
                issues.append(f"max_stay_days={visa.max_stay_days} (must be positive)")
            if visa.max_stay_days > 365:
                issues.append(
                    f"max_stay_days={visa.max_stay_days} (>365 is suspicious, "
                    "even Georgia caps at 365)"
                )

        return issues

    def _check_stay_days(self, visa: VisaRequirement) -> list[str]:
        """Check verified_at and expires_at are present and sane."""
        issues = []

        if visa.verified_at is None:
            issues.append("verified_at is None - data was never verified")

        if visa.expires_at is None:
            issues.append("expires_at is None - cache expiry not set")

        if visa.verified_at and visa.expires_at:
            if visa.expires_at <= visa.verified_at:
                issues.append("expires_at <= verified_at (cache expires before it starts)")

        return issues

    def _check_notes_quality(self, visa: VisaRequirement) -> list[str]:
        """Check notes for hallucination patterns."""
        issues = []

        if not visa.notes:
            return issues

        notes_lower = visa.notes.lower()
        for pattern in HALLUCINATION_PATTERNS:
            if re.search(pattern, notes_lower):
                issues.append(
                    f"Notes contain AI-hallucination pattern: '{pattern}'. "
                    "Source may be an AI-generated page, not official."
                )

        return issues

    def _check_transit_consistency(self, result: VisaCheckResult) -> list[str]:
        """Check transit visa logic."""
        issues = []

        for transit in result.transit_visas:
            if len(transit.transit_country) != 2:
                issues.append(f"Invalid transit country code: '{transit.transit_country}'")

            # If transit country = destination country, something is wrong
            if transit.transit_country == result.destination_visa.country_code:
                issues.append(
                    f"Transit country ({transit.transit_country}) == destination. "
                    "You can't transit through your destination."
                )

        return issues
