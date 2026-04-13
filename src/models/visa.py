"""Pydantic v2 schemas for visa intelligence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class VisaStatus(StrEnum):
    VISA_FREE = "visa_free"
    VISA_ON_ARRIVAL = "visa_on_arrival"
    E_VISA = "e_visa"
    VISA_REQUIRED = "visa_required"
    UNKNOWN = "unknown"


class VisaRequirement(BaseModel):
    """Visa requirements for a specific country + passport combination."""

    country_code: str = Field(min_length=2, max_length=2)
    country_name: str
    passport_type: str = Field(default="BY", examples=["BY", "RU"])
    visa_status: VisaStatus = VisaStatus.UNKNOWN
    max_stay_days: int | None = None
    notes: str | None = None
    source_url: str | None = None
    verified_at: datetime | None = None
    expires_at: datetime | None = None

    model_config = {"from_attributes": True}

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return True
        return datetime.now(self.expires_at.tzinfo) > self.expires_at

    @property
    def is_accessible(self) -> bool:
        """Can a Belarusian citizen enter without a pre-arranged visa?"""
        return self.visa_status in (
            VisaStatus.VISA_FREE,
            VisaStatus.VISA_ON_ARRIVAL,
            VisaStatus.E_VISA,
        )


class TransitRequirement(BaseModel):
    """Transit visa rules for passing through a country."""

    transit_country: str = Field(min_length=2, max_length=2)
    passport_type: str = Field(default="BY")
    visa_required: bool = True
    max_transit_hrs: int | None = None
    notes: str | None = None
    verified_at: datetime | None = None

    model_config = {"from_attributes": True}


class VisaCheckResult(BaseModel):
    """Combined result of visa + transit check for a deal."""

    destination_visa: VisaRequirement
    transit_visas: list[TransitRequirement] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def is_feasible(self) -> bool:
        """Can we do this trip without a traditional visa application?"""
        if not self.destination_visa.is_accessible:
            return False
        if any(t.visa_required for t in self.transit_visas):
            return False
        return True

    @property
    def is_data_stale(self) -> bool:
        """Check if visa data is older than 24 hours - may need refresh."""
        if not self.destination_visa.verified_at:
            return True
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        age = now - self.destination_visa.verified_at
        return age > timedelta(hours=24)

    @property
    def summary(self) -> str:
        parts = [f"Destination ({self.destination_visa.country_code}): {self.destination_visa.visa_status.value}"]
        for t in self.transit_visas:
            status = "VISA REQUIRED" if t.visa_required else "OK"
            parts.append(f"Transit ({t.transit_country}): {status}")
        if self.is_data_stale:
            parts.append("⚠️ DATA OLD - verify before booking!")
        if self.warnings:
            parts.append("Warnings: " + "; ".join(self.warnings))
        return " | ".join(parts)
