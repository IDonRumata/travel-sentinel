"""Travelata.ru scraper - package tours (flight + hotel) from Russia/Belarus.

LEGAL NOTE: Web scraping Travelata may violate their ToS.
For production use, consider their affiliate/partner API if available.
This implementation is for educational/MVP purposes only.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from bs4 import BeautifulSoup

from src.models.deals import DealCreate, DealType, MealPlan
from src.scrapers.base import BaseScraper

logger = structlog.get_logger()

# Departure city IDs on Travelata
DEPARTURE_CITIES = {
    "Moscow": {"code": "SVO", "city_id": 1},
    "St. Petersburg": {"code": "LED", "city_id": 2},
    "Minsk": {"code": "MSQ", "city_id": 2585},
}

# Country slugs on Travelata
TOUR_COUNTRIES = [
    ("TR", "turkey", "Turkey"),
    ("EG", "egypt", "Egypt"),
    ("TH", "thailand", "Thailand"),
    ("AE", "uae", "UAE"),
    ("LK", "srilanka", "Sri Lanka"),
    ("MV", "maldives", "Maldives"),
    ("CU", "cuba", "Cuba"),
    ("TN", "tunisia", "Tunisia"),
    ("VN", "vietnam", "Vietnam"),
    ("GE", "georgia", "Georgia"),
]


class TravelataScraper(BaseScraper):
    """Scrape package tour prices from Travelata search API."""

    def __init__(
        self,
        max_price_per_person: int = 400,
        adults: int = 2,
    ) -> None:
        super().__init__("travelata", max_price_per_person, adults)
        self._base_url = "https://travelata.ru/api/v3"

    async def scrape(self) -> list[DealCreate]:
        deals: list[DealCreate] = []
        check_in = date.today() + timedelta(days=30)

        for city_name, city_info in DEPARTURE_CITIES.items():
            for country_code, country_slug, country_name in TOUR_COUNTRIES:
                try:
                    found = await self._search_tours(
                        city_name=city_name,
                        city_code=city_info["code"],
                        city_id=city_info["city_id"],
                        country_code=country_code,
                        country_slug=country_slug,
                        country_name=country_name,
                        check_in=check_in,
                    )
                    deals.extend(found)
                except Exception as exc:
                    logger.warning(
                        "travelata.route_error",
                        city=city_name,
                        country=country_slug,
                        error=str(exc),
                    )
                    continue

        logger.info("travelata.total", deals=len(deals))
        return deals

    async def _search_tours(
        self,
        city_name: str,
        city_code: str,
        city_id: int,
        country_code: str,
        country_slug: str,
        country_name: str,
        check_in: date,
    ) -> list[DealCreate]:
        """Search tours via Travelata's internal API."""
        max_total_price = self.max_price_per_person * self.adults

        response = await self._fetch(
            f"{self._base_url}/search/tours",
            params={
                "cityFrom": city_id,
                "country": country_slug,
                "dateFrom": check_in.strftime("%d.%m.%Y"),
                "dateTo": (check_in + timedelta(days=14)).strftime("%d.%m.%Y"),
                "nightsMin": 7,
                "nightsMax": 14,
                "adults": self.adults,
                "priceMax": max_total_price,
                "currency": "usd",
                "sortBy": "price",
                "limit": 15,
            },
        )

        data = response.json()
        tours = data.get("data", {}).get("tours", [])
        results: list[DealCreate] = []

        for tour in tours:
            try:
                total_price = tour.get("price", {}).get("usd", 0)
                if total_price <= 0:
                    continue

                price_per_person = total_price / self.adults
                if price_per_person > self.max_price_per_person:
                    continue

                hotel = tour.get("hotel", {})
                meal_raw = tour.get("meal", "")
                meal = self._parse_meal(meal_raw)

                resort = tour.get("resort", {}).get("name", "")
                dest_str = f"{country_name}, {resort}" if resort else country_name

                depart_date_str = tour.get("dateFrom", "")
                return_date_str = tour.get("dateTo", "")

                tour_url = tour.get("link", "")
                if tour_url and not tour_url.startswith("http"):
                    tour_url = f"https://travelata.ru{tour_url}"

                results.append(
                    DealCreate(
                        deal_type=DealType.TOUR,
                        source="travelata",
                        destination=dest_str,
                        country_code=country_code,
                        departure_city=city_name,
                        departure_code=city_code,
                        departure_date=self._parse_date(depart_date_str),
                        return_date=self._parse_date(return_date_str) if return_date_str else None,
                        nights=tour.get("nights"),
                        price_eur=round(price_per_person, 2),
                        price_original=float(total_price),
                        currency="USD",
                        hotel_name=hotel.get("name"),
                        hotel_stars=hotel.get("stars"),
                        meal_plan=meal,
                        url=tour_url or f"https://travelata.ru/{country_slug}",
                    )
                )
            except Exception as exc:
                logger.warning("travelata.parse_error", error=str(exc))
                continue

        return results

    @staticmethod
    def _parse_meal(meal_str: str) -> MealPlan | None:
        mapping = {
            "all inclusive": MealPlan.ALL_INCLUSIVE,
            "ai": MealPlan.ALL_INCLUSIVE,
            "uai": MealPlan.ALL_INCLUSIVE,
            "half board": MealPlan.HALF_BOARD,
            "hb": MealPlan.HALF_BOARD,
            "bed & breakfast": MealPlan.BED_BREAKFAST,
            "bb": MealPlan.BED_BREAKFAST,
            "room only": MealPlan.ROOM_ONLY,
            "ro": MealPlan.ROOM_ONLY,
        }
        return mapping.get(meal_str.lower().strip())

    @staticmethod
    def _parse_date(date_str: str) -> date:
        """Parse date from dd.mm.yyyy or yyyy-mm-dd."""
        if "." in date_str:
            parts = date_str.split(".")
            return date(int(parts[2]), int(parts[1]), int(parts[0]))
        return date.fromisoformat(date_str)
