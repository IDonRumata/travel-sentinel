"""FastAPI application - tool endpoints for n8n AI Agent.

n8n calls these endpoints as "tools" that Claude can invoke:
1. /tools/search-deals - run scrapers, return cheap deals
2. /tools/check-visa - verify visa requirements
3. /tools/price-drops - find price drops
4. /health - liveness check
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.db.deals_repo import DealsRepository
from src.db.pool import close_pool, get_pool
from src.db.visa_repo import VisaRepository
from src.models.config import Settings
from src.models.visa import VisaCheckResult
from src.scrapers.orchestrator import ScraperOrchestrator
from src.visa.checker import VisaChecker
from src.visa.golden_tests import run_golden_tests
from src.visa.validator import VisaResultValidator

logger = structlog.get_logger()
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: manage DB pool."""
    pool = await get_pool(settings.postgres_dsn)
    app.state.pool = pool
    app.state.deals_repo = DealsRepository(pool)
    app.state.visa_repo = VisaRepository(pool)
    app.state.orchestrator = ScraperOrchestrator(settings, app.state.deals_repo)
    logger.info("app.started")
    yield
    await app.state.orchestrator.close()
    await close_pool()
    logger.info("app.stopped")


app = FastAPI(
    title="Travel Sentinel API",
    description="AI Agent tools for travel deal monitoring",
    version="0.1.0",
    lifespan=lifespan,
)


# === Request/Response schemas ===


class SearchDealsRequest(BaseModel):
    country_code: str | None = Field(default=None, description="Filter by country (ISO 2-letter)")
    max_price: float | None = Field(default=None, description="Max price per person USD")


class SearchDealsResponse(BaseModel):
    deals_found: int
    new_deals: int
    mode: str = "full"  # full | partial | cache_only
    data_freshness: str | None = None  # ISO timestamp of latest scrape
    deals: list[dict]


class VisaCheckRequest(BaseModel):
    country_code: str = Field(description="Destination country code")
    country_name: str = Field(description="Destination country name")
    transit_countries: list[str] = Field(default_factory=list, description="Transit country codes")


class PriceDropResponse(BaseModel):
    drops: list[dict]


# === Endpoints ===


@app.get("/health")
async def health():
    """Health check: DB ping + circuit breaker states.

    n8n should ping this every 5 minutes. If status != 'ok' -> alert.
    """
    # DB health check
    db_ok = False
    try:
        async with app.state.pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        db_ok = True
    except Exception as exc:
        logger.error("health.db_failed", error=str(exc))

    circuits = app.state.orchestrator.get_circuit_status()
    open_circuits = [c for c in circuits if c["state"] == "open"]

    status = "ok"
    if not db_ok:
        status = "degraded"
    elif open_circuits:
        status = "partial"

    return {
        "status": status,
        "service": "travel-sentinel",
        "db": "ok" if db_ok else "down",
        "scrapers": circuits,
        "open_circuits": [c["scraper"] for c in open_circuits],
    }


@app.post("/tools/search-deals", response_model=SearchDealsResponse)
async def search_deals(req: SearchDealsRequest):
    """Run all scrapers via Orchestrator with circuit breaker protection.

    Operational modes returned:
    - "full": all scrapers healthy
    - "partial": some scrapers failed, data may be incomplete
    - "cache_only": all scrapers down, data from DB cache
    """
    orchestrator: ScraperOrchestrator = app.state.orchestrator
    summary = await orchestrator.run_all()

    mode = summary["mode"]
    deals_raw = []

    if mode == "cache_only":
        # All scrapers dead - serve from DB
        cached = await orchestrator.get_fallback_deals(limit=30)
        deals_raw = [d.model_dump(mode="json") for d in cached]
        logger.warning("api.cache_only_mode")
    else:
        # Get fresh results from DB (already persisted by orchestrator)
        cached = await app.state.deals_repo.get_cheapest(
            country_code=req.country_code,
            max_price=req.max_price or settings.max_price_per_person_usd,
            limit=30,
        )
        deals_raw = [d.model_dump(mode="json") for d in cached]

    # Compute data freshness from latest deal's scraped_at
    freshness = None
    if cached:
        latest = max(d.scraped_at for d in cached)
        freshness = latest.isoformat()

    return SearchDealsResponse(
        deals_found=len(deals_raw),
        new_deals=summary["deals_new"],
        mode=mode,
        data_freshness=freshness,
        deals=deals_raw,
    )


@app.post("/tools/check-visa", response_model=VisaCheckResult)
async def check_visa(req: VisaCheckRequest):
    """Check visa requirements for a destination + transit points.

    Critical tool - the "Devil's Advocate" that prevents booking mistakes.

    Warnings to watch for:
    - is_data_stale: True if verified > 24h ago (visa rules can change overnight)
    - warnings array: flags like Schengen requirements, UNKNOWN status, etc.
    - is_feasible: False = DO NOT RECOMMEND THIS TRIP
    """
    checker = VisaChecker(
        visa_repo=app.state.visa_repo,
        brave_api_key=settings.brave_search_api_key,  # May be None (hybrid mode)
        passport_type=settings.passport_type,
    )
    try:
        result = await checker.check_deal(
            country_code=req.country_code,
            country_name=req.country_name,
            transit_countries=req.transit_countries,
        )

        # Level 1: Deterministic validation (free)
        validator = VisaResultValidator()
        issues = validator.validate(result)
        if issues:
            result.warnings.extend([f"VALIDATION: {issue}" for issue in issues])

        # Add warning if data is stale
        if result.is_data_stale:
            result.warnings.append(
                "⚠️ STALE DATA: Visa info is > 24 hours old. "
                "Verify with official source before final booking decision."
            )

        logger.info(
            "api.visa_checked",
            country=req.country_code,
            feasible=result.is_feasible,
            stale=result.is_data_stale,
        )
        return result
    except Exception as exc:
        logger.error("api.visa_check_error", error=str(exc))
        raise HTTPException(status_code=502, detail=f"Visa check failed: {exc}")
    finally:
        await checker.close()


@app.get("/tools/price-drops", response_model=PriceDropResponse)
async def price_drops(
    threshold: int = Query(default=10, ge=1, le=50, description="Min drop percentage"),
):
    """Find deals with significant price drops since last check."""
    repo = app.state.deals_repo
    drops = await repo.get_price_drops(threshold)
    return PriceDropResponse(drops=drops)


@app.get("/tools/cheapest")
async def cheapest_deals(
    country: str | None = Query(default=None),
    max_price: float | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Get cheapest deals from database (no scraping, just cache)."""
    repo = app.state.deals_repo
    deals = await repo.get_cheapest(
        country_code=country,
        max_price=max_price or settings.max_price_per_person_usd,
        limit=limit,
    )
    return {"deals": [d.model_dump(mode="json") for d in deals]}


# === Observability endpoints ===


@app.post("/ops/golden-tests")
async def run_golden_test_suite():
    """Run golden test suite against VisaChecker.

    Level 2 of Quality Pyramid: known correct answers for 17 countries.
    Run weekly via n8n cron. If accuracy < 85%, send Telegram alert.
    """
    checker = VisaChecker(
        visa_repo=app.state.visa_repo,
        brave_api_key=settings.brave_search_api_key,  # May be None (hybrid mode)
        passport_type=settings.passport_type,
    )
    try:
        result = await run_golden_tests(
            check_fn=checker._check_country,
            threshold=0.85,
        )
        return {
            "total": result.total,
            "passed": result.passed,
            "failed": result.failed,
            "accuracy": result.accuracy,
            "threshold": 0.85,
            "status": "PASS" if result.accuracy >= 0.85 else "FAIL",
            "failures": result.failures,
            "timestamp": result.timestamp,
        }
    finally:
        await checker.close()


@app.get("/ops/stats")
async def operational_stats():
    """Operational statistics for the dashboard.

    Shows: scraper health, cache freshness, rejection rates.
    """
    repo = app.state.deals_repo
    circuits = app.state.orchestrator.get_circuit_status()

    # Get latest scrape info
    async with app.state.pool.acquire() as conn:
        # Total deals in DB
        total_deals = await conn.fetchval("SELECT COUNT(*) FROM deals")

        # Deals scraped today
        today_deals = await conn.fetchval(
            "SELECT COUNT(*) FROM deals WHERE scraped_at > CURRENT_DATE"
        )

        # Latest scrape timestamp
        latest_scrape = await conn.fetchval(
            "SELECT MAX(scraped_at) FROM deals"
        )

        # Scraper runs today
        scrape_runs = await conn.fetch(
            """
            SELECT scraper_name, status, COUNT(*) as runs,
                   SUM(deals_found) as total_found, SUM(deals_new) as total_new
            FROM scrape_log
            WHERE started_at > CURRENT_DATE
            GROUP BY scraper_name, status
            """
        )

        # Visa checks today
        visa_cache_count = await conn.fetchval(
            "SELECT COUNT(*) FROM visa_requirements WHERE expires_at > NOW()"
        )

    return {
        "deals": {
            "total_in_db": total_deals,
            "scraped_today": today_deals,
            "latest_scrape": latest_scrape.isoformat() if latest_scrape else None,
        },
        "scrapers": {
            "circuits": circuits,
            "runs_today": [dict(r) for r in scrape_runs],
        },
        "visa_cache": {
            "active_entries": visa_cache_count,
        },
    }
