"""Repository for deal CRUD operations."""

from __future__ import annotations

from datetime import datetime

import asyncpg
import structlog

from src.models.deals import Deal, DealCreate

logger = structlog.get_logger()


class DealsRepository:
    """Data access layer for deals table - no business logic here."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_deal(self, deal: DealCreate) -> tuple[int, bool]:
        """Insert a deal if checksum doesn't exist. Returns (deal_id, is_new)."""
        async with self._pool.acquire() as conn:
            # Try insert, on conflict return existing id
            row = await conn.fetchrow(
                """
                INSERT INTO deals (
                    deal_type, source, destination, country_code,
                    departure_city, departure_code, departure_date, return_date,
                    nights, price_eur, price_original, currency,
                    hotel_name, hotel_stars, meal_plan, url, checksum
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                ON CONFLICT (checksum) DO UPDATE SET price_eur = EXCLUDED.price_eur
                RETURNING id, (xmax = 0) AS is_new
                """,
                deal.deal_type.value,
                deal.source,
                deal.destination,
                deal.country_code.upper(),
                deal.departure_city,
                deal.departure_code.upper(),
                deal.departure_date,
                deal.return_date,
                deal.nights,
                deal.price_eur,
                deal.price_original,
                deal.currency,
                deal.hotel_name,
                deal.hotel_stars,
                deal.meal_plan.value if deal.meal_plan else None,
                deal.url,
                deal.checksum,
            )
            is_new = row["is_new"]
            deal_id = row["id"]

            # Track price history
            await conn.execute(
                "INSERT INTO price_history (deal_id, price_eur) VALUES ($1, $2)",
                deal_id,
                deal.price_eur,
            )

            logger.info(
                "deal.upserted",
                deal_id=deal_id,
                is_new=is_new,
                source=deal.source,
                destination=deal.destination,
                price=deal.price_eur,
            )
            return deal_id, is_new

    async def get_cheapest(
        self,
        country_code: str | None = None,
        max_price: float | None = None,
        limit: int = 20,
    ) -> list[Deal]:
        """Fetch cheapest deals with optional filters."""
        conditions = ["departure_date > NOW()"]
        params: list = []
        idx = 1

        if country_code:
            conditions.append(f"country_code = ${idx}")
            params.append(country_code.upper())
            idx += 1

        if max_price:
            conditions.append(f"price_eur <= ${idx}")
            params.append(max_price)
            idx += 1

        where = " AND ".join(conditions)
        params.append(limit)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT * FROM deals WHERE {where} ORDER BY price_eur ASC LIMIT ${idx}",
                *params,
            )
            return [Deal(**dict(r)) for r in rows]

    async def get_price_drops(self, threshold_percent: int = 10) -> list[dict]:
        """Find deals where latest price dropped by threshold% vs previous."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH ranked AS (
                    SELECT
                        deal_id,
                        price_eur,
                        recorded_at,
                        LAG(price_eur) OVER (PARTITION BY deal_id ORDER BY recorded_at) AS prev_price
                    FROM price_history
                )
                SELECT
                    d.*,
                    r.prev_price,
                    r.price_eur AS new_price,
                    ROUND(((r.prev_price - r.price_eur) / r.prev_price * 100)::numeric, 1) AS drop_percent
                FROM ranked r
                JOIN deals d ON d.id = r.deal_id
                WHERE r.prev_price IS NOT NULL
                  AND r.prev_price > r.price_eur
                  AND ((r.prev_price - r.price_eur) / r.prev_price * 100) >= $1
                ORDER BY drop_percent DESC
                """,
                float(threshold_percent),
            )
            return [dict(r) for r in rows]

    async def log_scrape(
        self,
        scraper_name: str,
        status: str,
        deals_found: int,
        deals_new: int,
        duration_ms: int,
        error_message: str | None = None,
    ) -> None:
        """Record a scraper run in the log."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO scrape_log
                    (scraper_name, status, deals_found, deals_new, duration_ms, error_message, finished_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW())
                """,
                scraper_name,
                status,
                deals_found,
                deals_new,
                duration_ms,
                error_message,
            )
