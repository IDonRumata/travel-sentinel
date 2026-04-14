"""Aviasales API scraper - flight deals from Belarusian/Russian airports."""

from __future__ import annotations

from datetime import date, timedelta

import structlog

from src.models.deals import DealCreate, DealType
from src.scrapers.base import BaseScraper
from src.scrapers.iata_countries import get_transit_countries

logger = structlog.get_logger()

# Airports we monitor
DEPARTURE_AIRPORTS = ["MSQ", "SVO", "VKO", "DME", "LED"]

# Popular visa-free / e-visa destinations for BY citizens
TARGET_DESTINATIONS = [
    ("TR", "IST", "Turkey, Istanbul"),
    ("TR", "AYT", "Turkey, Antalya"),
    ("EG", "HRG", "Egypt, Hurghada"),
    ("EG", "SSH", "Egypt, Sharm El Sheikh"),
    ("GE", "TBS", "Georgia, Tbilisi"),
    ("AM", "EVN", "Armenia, Yerevan"),
    ("AE", "DXB", "UAE, Dubai"),
    ("TH", "BKK", "Thailand, Bangkok"),
    ("LK", "CMB", "Sri Lanka, Colombo"),
    ("VN", "SGN", "Vietnam, Ho Chi Minh"),
    ("MV", "MLE", "Maldives, Male"),
    ("CU", "HAV", "Cuba, Havana"),
    ("RS", "BEG", "Serbia, Belgrade"),
    ("ME", "TGD", "Montenegro, Podgorica"),
    ("TN", "TUN", "Tunisia, Tunis"),
    ("AZ", "GYD", "Azerbaijan, Baku"),
    ("UZ", "TAS", "Uzbekistan, Tashkent"),
    ("KG", "FRU", "Kyrgyzstan, Bishkek"),
]


class AviasalesScraper(BaseScraper):
    """Scrape cheap flights via Aviasales API (Travelpayouts partner API).

    API docs: https://support.travelpayouts.com/hc/en-us/articles/203956163
    Uses the /v1/prices/cheap endpoint for price calendar.
    """

    def __init__(
        self,
        api_token: str,
        max_price_per_person: int = 400,
        adults: int = 2,
    ) -> None:
        super().__init__("aviasales", max_price_per_person, adults)
        self._token = api_token
        self._base_url = "https://api.travelpayouts.com"

    async def scrape(self) -> list[DealCreate]:
        deals: list[DealCreate] = []
        # Search window: next 30-90 days
        depart_month = date.today() + timedelta(days=30)

        for origin in DEPARTURE_AIRPORTS:
            for country_code, dest_iata, dest_name in TARGET_DESTINATIONS:
                try:
                    found = await self._search_route(
                        origin, dest_iata, country_code, dest_name, depart_month
                    )
                    deals.extend(found)
                except Exception as exc:
                    logger.warning(
                        "aviasales.route_error",
                        origin=origin,
                        dest=dest_iata,
                        error=str(exc),
                    )
                    continue

        logger.info("aviasales.total", deals=len(deals))
        return deals

    async def _search_route(
        self,
        origin: str,
        destination: str,
        country_code: str,
        dest_name: str,
        depart_month: date,
    ) -> list[DealCreate]:
        """Search one route via the cheap prices endpoint."""
        response = await self._fetch(
            f"{self._base_url}/aviasales/v3/prices_for_dates",
            params={
                "origin": origin,
                "destination": destination,
                "departure_at": depart_month.strftime("%Y-%m"),
                "return_at": (depart_month + timedelta(days=14)).strftime("%Y-%m"),
                "sorting": "price",
                "direct": "false",
                "cy": "usd",
                "limit": 10,
                "page": 1,
                "one_way": "false",
                "token": self._token,
            },
        )

        data = response.json()
        if not data.get("success"):
            return []

        results: list[DealCreate] = []
        for ticket in data.get("data", []):
            price = ticket.get("price", 0)
            # price is total for all passengers, convert to per-person
            price_per_person = price / self.adults if self.adults > 0 else price

            if price_per_person > self.max_price_per_person:
                continue

            departure_at = ticket.get("departure_at", "")[:10]
            return_at = ticket.get("return_at", "")[:10]

            if not departure_at:
                continue

            origin_city = self._airport_to_city(origin)
            link = ticket.get("link", "")
            full_url = f"https://www.aviasales.ru{link}" if link else f"https://www.aviasales.ru/search/{origin}{departure_at.replace('-','')}{destination}1"

            # Extract transit countries from route
            # Aviasales API returns route as list of IATA codes in ticket["route"]
            route_airports = ticket.get("route", [origin, destination])
            transit_countries = get_transit_countries(route_airports)

            if transit_countries:
                logger.info(
                    "aviasales.transit_detected",
                    route=route_airports,
                    transit_countries=transit_countries,
                )

            results.append(
                DealCreate(
                    deal_type=DealType.FLIGHT,
                    source="aviasales",
                    destination=dest_name,
                    country_code=country_code,
                    departure_city=origin_city,
                    departure_code=origin,
                    departure_date=date.fromisoformat(departure_at),
                    return_date=date.fromisoformat(return_at) if return_at else None,
                    nights=ticket.get("duration_to"),
                    price_eur=round(price_per_person, 2),
                    price_original=float(price),
                    currency="USD",
                    url=full_url,
                    transit_countries=transit_countries,
                )
            )

        return results

    @staticmethod
    def _airport_to_city(code: str) -> str:
        mapping = {
            "MSQ": "Minsk",
            "SVO": "Moscow",
            "VKO": "Moscow",
            "DME": "Moscow",
            "LED": "St. Petersburg",
        }
        return mapping.get(code, code)
