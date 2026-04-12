"""Repository for visa requirements cache."""

from __future__ import annotations

import asyncpg
import structlog

from src.models.visa import TransitRequirement, VisaRequirement, VisaStatus

logger = structlog.get_logger()


class VisaRepository:
    """Data access layer for visa/transit requirements cache."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_visa(
        self, country_code: str, passport_type: str = "BY"
    ) -> VisaRequirement | None:
        """Get cached visa requirement if not expired."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM visa_requirements
                WHERE country_code = $1 AND passport_type = $2 AND expires_at > NOW()
                """,
                country_code.upper(),
                passport_type.upper(),
            )
            if row:
                logger.info("visa.cache_hit", country=country_code, passport=passport_type)
                return VisaRequirement(**dict(row))
            logger.info("visa.cache_miss", country=country_code, passport=passport_type)
            return None

    async def save_visa(self, req: VisaRequirement) -> None:
        """Upsert visa requirement into cache."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO visa_requirements
                    (country_code, country_name, passport_type, visa_status,
                     max_stay_days, notes, source_url, verified_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (country_code, passport_type) DO UPDATE SET
                    visa_status = EXCLUDED.visa_status,
                    max_stay_days = EXCLUDED.max_stay_days,
                    notes = EXCLUDED.notes,
                    source_url = EXCLUDED.source_url,
                    verified_at = EXCLUDED.verified_at,
                    expires_at = EXCLUDED.expires_at
                """,
                req.country_code.upper(),
                req.country_name,
                req.passport_type.upper(),
                req.visa_status.value,
                req.max_stay_days,
                req.notes,
                req.source_url,
                req.verified_at,
                req.expires_at,
            )
            logger.info("visa.saved", country=req.country_code, status=req.visa_status.value)

    async def get_transit(
        self, transit_country: str, passport_type: str = "BY"
    ) -> TransitRequirement | None:
        """Get cached transit requirement."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM transit_requirements
                WHERE transit_country = $1 AND passport_type = $2 AND expires_at > NOW()
                """,
                transit_country.upper(),
                passport_type.upper(),
            )
            if row:
                return TransitRequirement(**dict(row))
            return None

    async def save_transit(self, req: TransitRequirement) -> None:
        """Upsert transit requirement."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO transit_requirements
                    (transit_country, passport_type, visa_required, max_transit_hrs, notes, verified_at, expires_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (transit_country, passport_type) DO UPDATE SET
                    visa_required = EXCLUDED.visa_required,
                    max_transit_hrs = EXCLUDED.max_transit_hrs,
                    notes = EXCLUDED.notes,
                    verified_at = EXCLUDED.verified_at,
                    expires_at = EXCLUDED.expires_at
                """,
                req.transit_country.upper(),
                req.passport_type.upper(),
                req.visa_required,
                req.max_transit_hrs,
                req.notes,
                req.verified_at,
                req.verified_at,  # expires_at defaults to verified_at + 7 days in DB
            )
