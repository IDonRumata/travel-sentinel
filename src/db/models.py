"""SQLAlchemy 2.0 ORM models for Travel Sentinel.

Used alongside asyncpg for direct queries (asyncpg is faster for bulk reads).
SQLAlchemy ORM is used for structured writes and complex joins.

SECURITY NOTE: user_has_active_visas is NEVER stored in DB.
It is stateless - passed per-request only. See PRIVACY_NOTES below.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    JSON,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# PRIVACY NOTES:
# - user_has_active_visas: NEVER stored. Stateless per-request parameter only.
# - transit_countries: stored as ISO codes (country-level), not personal data.
# - No user identifiers in any table. This system is single-user (Andrey).
# - Logging: active visas must be masked before log output (see logging_config.py)


class Base(DeclarativeBase):
    pass


class VisaRuleDetail:
    """Pydantic-compatible schema for rules_data JSON column.

    Stored in visa_requirements.rules_data and transit_requirements.twov_notes.
    Provides structured access to complex visa/TWOV conditions.
    """

    def __init__(
        self,
        status: str,
        max_stay: int | None = None,
        twov_allowed: bool = False,
        twov_required_visas: list[str] | None = None,
        twov_max_hours: int | None = None,
        source_url: str = "",
        notes: str = "",
    ) -> None:
        self.status = status
        self.max_stay = max_stay
        self.twov_allowed = twov_allowed
        self.twov_required_visas = twov_required_visas or []
        self.twov_max_hours = twov_max_hours
        self.source_url = source_url
        self.notes = notes

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "max_stay": self.max_stay,
            "twov_allowed": self.twov_allowed,
            "twov_required_visas": self.twov_required_visas,
            "twov_max_hours": self.twov_max_hours,
            "source_url": self.source_url,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VisaRuleDetail:
        return cls(**data)


class VisaCacheModel(Base):
    """SQLAlchemy 2.0 model for visa_requirements table.

    Mirrors PostgreSQL schema from 001_init.sql + 002_visa_rules_twov.sql.
    """

    __tablename__ = "visa_requirements"
    __table_args__ = (
        UniqueConstraint("country_code", "passport_type", name="uq_visa_country_passport"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), index=True, nullable=False)
    country_name: Mapped[str] = mapped_column(String(200), nullable=False)
    passport_type: Mapped[str] = mapped_column(String(20), nullable=False, default="BY")
    visa_status: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")
    max_stay_days: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Structured TWOV rules stored as JSONB for flexibility
    rules_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    twov_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    twov_required_visas: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def is_expired(self, ttl_hours: int = 24) -> bool:
        """Check if this cache entry is stale.

        Default TTL: 24 hours (visa rules can change fast).
        CacheWarmer uses 168h (7 days) for its own full refresh cycle.
        """
        delta = datetime.now(timezone.utc) - self.verified_at
        return delta.total_seconds() > ttl_hours * 3600

    def get_rule_detail(self) -> VisaRuleDetail | None:
        """Deserialize rules_data JSON into typed VisaRuleDetail."""
        if not self.rules_data:
            return None
        return VisaRuleDetail.from_dict(self.rules_data)


class TransitCacheModel(Base):
    """SQLAlchemy 2.0 model for transit_requirements table."""

    __tablename__ = "transit_requirements"
    __table_args__ = (
        UniqueConstraint(
            "transit_country", "passport_type", name="uq_transit_country_passport"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    transit_country: Mapped[str] = mapped_column(String(2), nullable=False)
    passport_type: Mapped[str] = mapped_column(String(20), nullable=False, default="BY")
    visa_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_transit_hrs: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # TWOV fields (from migration 002)
    twov_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    twov_required_visas: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    twov_max_hours: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    twov_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    def can_transit_without_visa(self, user_active_visas: list[str]) -> bool:
        """Check if TWOV applies given the traveler's active visas.

        Args:
            user_active_visas: Active visa types user holds (e.g. ["US", "Schengen"])
                               NEVER stored to DB - stateless per-request only.

        Returns:
            True if transit is possible without a pre-arranged transit visa.
        """
        if not self.twov_allowed or not self.twov_required_visas:
            return False

        normalized = {v.upper() for v in user_active_visas}
        required = {v.upper() for v in self.twov_required_visas}

        return bool(normalized & required)  # any overlap = TWOV possible
