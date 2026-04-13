"""Golden Test Suite for VisaChecker accuracy monitoring.

Level 2 of the Quality Pyramid: run weekly, compare against known ground truth.
If accuracy drops below threshold, something changed (model update, API change, etc.)

IMPORTANT: These are KNOWN correct answers as of 2026-04.
Update this file when visa rules change for real.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

from src.models.visa import VisaStatus

logger = structlog.get_logger()


@dataclass
class GoldenCase:
    """One known-correct visa case for regression testing."""
    country_code: str
    country_name: str
    passport_type: str
    expected_status: VisaStatus
    notes: str  # why this is the expected answer


# Ground truth as of April 2026 for Belarusian passport
GOLDEN_CASES_BY: list[GoldenCase] = [
    # === VISA FREE (well-established) ===
    GoldenCase("TR", "Turkey", "BY", VisaStatus.VISA_FREE,
               "30 days visa-free, confirmed bilateral agreement"),
    GoldenCase("GE", "Georgia", "BY", VisaStatus.VISA_FREE,
               "365 days visa-free for BY citizens"),
    GoldenCase("AM", "Armenia", "BY", VisaStatus.VISA_FREE,
               "180 days visa-free, CIS agreement"),
    GoldenCase("AZ", "Azerbaijan", "BY", VisaStatus.VISA_FREE,
               "90 days visa-free"),
    GoldenCase("RS", "Serbia", "BY", VisaStatus.VISA_FREE,
               "30 days visa-free"),
    GoldenCase("ME", "Montenegro", "BY", VisaStatus.VISA_FREE,
               "30 days visa-free (seasonal, check dates)"),
    GoldenCase("UZ", "Uzbekistan", "BY", VisaStatus.VISA_FREE,
               "90 days visa-free, CIS agreement"),
    GoldenCase("KG", "Kyrgyzstan", "BY", VisaStatus.VISA_FREE,
               "90 days visa-free, CIS agreement"),
    GoldenCase("CU", "Cuba", "BY", VisaStatus.VISA_FREE,
               "30 days visa-free"),

    # === VISA ON ARRIVAL or E-VISA ===
    GoldenCase("EG", "Egypt", "BY", VisaStatus.VISA_ON_ARRIVAL,
               "Visa on arrival at airport, ~25 USD"),
    GoldenCase("MV", "Maldives", "BY", VisaStatus.VISA_ON_ARRIVAL,
               "30 days free visa on arrival"),
    GoldenCase("LK", "Sri Lanka", "BY", VisaStatus.E_VISA,
               "ETA (e-visa) required before travel"),

    # === VISA REQUIRED (Schengen, etc.) ===
    GoldenCase("DE", "Germany", "BY", VisaStatus.VISA_REQUIRED,
               "Schengen visa required"),
    GoldenCase("FR", "France", "BY", VisaStatus.VISA_REQUIRED,
               "Schengen visa required"),
    GoldenCase("US", "United States", "BY", VisaStatus.VISA_REQUIRED,
               "B1/B2 visa required, interview at embassy"),
    GoldenCase("GB", "United Kingdom", "BY", VisaStatus.VISA_REQUIRED,
               "UK visa required"),
    GoldenCase("JP", "Japan", "BY", VisaStatus.VISA_REQUIRED,
               "Visa required, apply through embassy"),
]


@dataclass
class GoldenTestResult:
    """Result of running golden tests."""
    total: int
    passed: int
    failed: int
    accuracy: float
    failures: list[dict]
    timestamp: str


async def run_golden_tests(
    check_fn,
    threshold: float = 0.85,
) -> GoldenTestResult:
    """Run all golden cases against the visa checker.

    Args:
        check_fn: async function(country_code, country_name) -> VisaRequirement
        threshold: minimum accuracy to pass (0.85 = 85%)

    Returns:
        GoldenTestResult with pass/fail details
    """
    passed = 0
    failed = 0
    failures: list[dict] = []

    for case in GOLDEN_CASES_BY:
        try:
            result = await check_fn(case.country_code, case.country_name)
            actual_status = result.visa_status

            if actual_status == case.expected_status:
                passed += 1
                logger.info(
                    "golden.passed",
                    country=case.country_code,
                    expected=case.expected_status.value,
                )
            else:
                failed += 1
                failure = {
                    "country": case.country_code,
                    "country_name": case.country_name,
                    "expected": case.expected_status.value,
                    "actual": actual_status.value,
                    "notes": case.notes,
                }
                failures.append(failure)
                logger.error("golden.failed", **failure)

        except Exception as exc:
            failed += 1
            failures.append({
                "country": case.country_code,
                "expected": case.expected_status.value,
                "actual": "ERROR",
                "error": str(exc),
            })
            logger.error("golden.error", country=case.country_code, error=str(exc))

    total = passed + failed
    accuracy = passed / total if total > 0 else 0.0

    result = GoldenTestResult(
        total=total,
        passed=passed,
        failed=failed,
        accuracy=round(accuracy, 3),
        failures=failures,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    if accuracy < threshold:
        logger.error(
            "golden.below_threshold",
            accuracy=accuracy,
            threshold=threshold,
            failed_countries=[f["country"] for f in failures],
        )
    else:
        logger.info("golden.suite_passed", accuracy=accuracy, total=total)

    return result
