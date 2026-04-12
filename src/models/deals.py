"""Pydantic v2 schemas for travel deals."""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class DealType(StrEnum):
    FLIGHT = "flight"
    TOUR = "tour"
    CRUISE = "cruise"


class MealPlan(StrEnum):
    ALL_INCLUSIVE = "AI"
    HALF_BOARD = "HB"
    BED_BREAKFAST = "BB"
    ROOM_ONLY = "RO"


class DealCreate(BaseModel):
    """Schema for creating a new deal (input from scrapers)."""

    deal_type: DealType
    source: str = Field(max_length=100, examples=["aviasales", "travelata"])
    destination: str = Field(max_length=200, examples=["Turkey, Antalya"])
    country_code: str = Field(min_length=2, max_length=2, examples=["TR"])
    departure_city: str = Field(max_length=100, examples=["Minsk"])
    departure_code: str = Field(max_length=5, examples=["MSQ"])
    departure_date: date
    return_date: date | None = None
    nights: int | None = Field(default=None, ge=1, le=90)
    price_eur: float = Field(gt=0)
    price_original: float | None = None
    currency: str = Field(default="EUR", max_length=3)
    hotel_name: str | None = Field(default=None, max_length=300)
    hotel_stars: int | None = Field(default=None, ge=1, le=5)
    meal_plan: MealPlan | None = None
    url: str

    @computed_field
    @property
    def checksum(self) -> str:
        """SHA256 from key fields for deduplication."""
        key = f"{self.source}|{self.destination}|{self.departure_code}|{self.departure_date}|{self.price_eur}|{self.url}"
        return hashlib.sha256(key.encode()).hexdigest()


class Deal(DealCreate):
    """Full deal record from database."""

    id: int
    checksum: str  # type: ignore[assignment]
    scraped_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class PricePoint(BaseModel):
    """Single price observation for price history tracking."""

    deal_id: int
    price_eur: float
    recorded_at: datetime

    model_config = {"from_attributes": True}
