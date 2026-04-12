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
from src.scrapers.aviasales import AviasalesScraper
from src.scrapers.travelata import TravelataScraper
from src.visa.checker import VisaChecker

logger = structlog.get_logger()
settings = Settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown: manage DB pool."""
    pool = await get_pool(settings.postgres_dsn)
    app.state.pool = pool
    app.state.deals_repo = DealsRepository(pool)
    app.state.visa_repo = VisaRepository(pool)
    logger.info("app.started")
    yield
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
    return {"status": "ok", "service": "travel-sentinel"}


@app.post("/tools/search-deals", response_model=SearchDealsResponse)
async def search_deals(req: SearchDealsRequest):
    """Run all scrapers and return filtered deals.

    This is the main "search" tool that n8n AI Agent calls.
    """
    max_pp = req.max_price or settings.max_price_per_person_usd
    repo = app.state.deals_repo

    # Run scrapers
    all_deals = []
    scrapers = [
        AviasalesScraper(
            api_token=settings.aviasales_token,
            max_price_per_person=int(max_pp),
            adults=settings.adults,
        ),
        TravelataScraper(
            max_price_per_person=int(max_pp),
            adults=settings.adults,
        ),
    ]

    for scraper in scrapers:
        try:
            deals = await scraper.run()
            all_deals.extend(deals)
        except Exception as exc:
            logger.error("api.scraper_error", scraper=scraper.name, error=str(exc))
        finally:
            await scraper.close()

    # Persist to DB
    new_count = 0
    for deal in all_deals:
        try:
            _, is_new = await repo.upsert_deal(deal)
            if is_new:
                new_count += 1
        except Exception as exc:
            logger.error("api.upsert_error", error=str(exc))

    # Filter by country if specified
    if req.country_code:
        all_deals = [d for d in all_deals if d.country_code.upper() == req.country_code.upper()]

    return SearchDealsResponse(
        deals_found=len(all_deals),
        new_deals=new_count,
        deals=[d.model_dump(mode="json") for d in all_deals[:30]],
    )


@app.post("/tools/check-visa", response_model=VisaCheckResult)
async def check_visa(req: VisaCheckRequest):
    """Check visa requirements for a destination + transit points.

    Critical tool - the "Devil's Advocate" that prevents booking mistakes.
    """
    checker = VisaChecker(
        visa_repo=app.state.visa_repo,
        brave_api_key=settings.brave_search_api_key,
        passport_type=settings.passport_type,
    )
    try:
        result = await checker.check_deal(
            country_code=req.country_code,
            country_name=req.country_name,
            transit_countries=req.transit_countries,
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
