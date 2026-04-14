"""IATA airport code → ISO country code mapping.

Used to detect transit countries from flight routes.
Critical for transit visa checks (e.g., LHR = UK = visa required for BY citizens).
"""

# Major transit hubs only - airports where BY citizens could be transiting
# Format: IATA_CODE -> ISO_COUNTRY_CODE
AIRPORT_TO_COUNTRY: dict[str, str] = {
    # --- SCHENGEN ZONE (transit visa usually required for BY) ---
    # Germany
    "FRA": "DE", "MUC": "DE", "DUS": "DE", "TXL": "DE", "BER": "DE",
    # Netherlands
    "AMS": "NL",
    # France
    "CDG": "FR", "ORY": "FR",
    # Spain
    "MAD": "ES", "BCN": "ES",
    # Italy
    "FCO": "IT", "MXP": "IT",
    # Austria
    "VIE": "AT",
    # Switzerland (not Schengen but similar)
    "ZRH": "CH", "GVA": "CH",
    # Sweden
    "ARN": "SE",
    # Finland
    "HEL": "FI",
    # Denmark
    "CPH": "DK",
    # Poland
    "WAW": "PL", "KRK": "PL",
    # Czech Republic
    "PRG": "CZ",
    # Hungary
    "BUD": "HU",
    # Greece
    "ATH": "GR",
    # Portugal
    "LIS": "PT",
    # Belgium
    "BRU": "BE",

    # --- UK (visa required for BY) ---
    "LHR": "GB", "LGW": "GB", "STN": "GB", "LCY": "GB", "MAN": "GB",

    # --- USA (visa required for BY) ---
    "JFK": "US", "LAX": "US", "ORD": "US", "ATL": "US", "MIA": "US",
    "IAD": "US", "SFO": "US", "DFW": "US", "EWR": "US", "BOS": "US",

    # --- Canada (visa required for BY) ---
    "YYZ": "CA", "YVR": "CA", "YUL": "CA",

    # --- Australia (visa required for BY) ---
    "SYD": "AU", "MEL": "AU", "BNE": "AU",

    # --- Japan (visa required for BY) ---
    "NRT": "JP", "HND": "JP", "KIX": "JP",

    # --- South Korea (e-visa for BY) ---
    "ICN": "KR", "GMP": "KR",

    # --- China (visa usually required) ---
    "PEK": "CN", "PVG": "CN", "CAN": "CN", "CTU": "CN",

    # --- India (e-visa available) ---
    "DEL": "IN", "BOM": "IN", "MAA": "IN",

    # --- Middle East hubs (mostly visa-free or VOA for BY) ---
    "DXB": "AE", "AUH": "AE", "SHJ": "AE",  # UAE
    "DOH": "QA",                               # Qatar
    "BAH": "BH",                               # Bahrain
    "AMM": "JO",                               # Jordan
    "BEY": "LB",                               # Lebanon
    "TLV": "IL",                               # Israel (visa required for BY)
    "RUH": "SA", "JED": "SA",                  # Saudi Arabia (e-visa)
    "MCT": "OM",                               # Oman

    # --- Turkey (visa-free hub, popular transit) ---
    "IST": "TR", "SAW": "TR", "AYT": "TR",

    # --- Georgia (visa-free) ---
    "TBS": "GE",

    # --- Armenia (visa-free) ---
    "EVN": "AM",

    # --- Russia (visa-free for BY) ---
    "SVO": "RU", "DME": "RU", "VKO": "RU", "LED": "RU", "SVX": "RU",

    # --- CIS (mostly visa-free for BY) ---
    "GYD": "AZ",  # Azerbaijan, Baku
    "TAS": "UZ",  # Uzbekistan, Tashkent
    "FRU": "KG",  # Kyrgyzstan, Bishkek
    "NQZ": "KZ",  # Kazakhstan, Astana
    "ALA": "KZ",  # Kazakhstan, Almaty

    # --- Thailand (visa-free) ---
    "BKK": "TH", "DMK": "TH", "HKT": "TH",

    # --- Malaysia (visa-free) ---
    "KUL": "MY",

    # --- Singapore (check current rules) ---
    "SIN": "SG",

    # --- Vietnam (15 days visa-free) ---
    "SGN": "VN", "HAN": "VN",

    # --- Indonesia (VOA) ---
    "CGK": "ID", "DPS": "ID",

    # --- Sri Lanka (e-visa) ---
    "CMB": "LK",

    # --- Maldives (VOA) ---
    "MLE": "MV",

    # --- Egypt (VOA) ---
    "CAI": "EG", "HRG": "EG", "SSH": "EG",

    # --- Morocco (visa-free) ---
    "CMN": "MA", "RAK": "MA",

    # --- Ethiopia hub ---
    "ADD": "ET",  # Addis Ababa - major African hub (visa required)

    # --- Kenya hub ---
    "NBO": "KE",  # Nairobi (e-visa)

    # --- South Africa ---
    "JNB": "ZA",  # Johannesburg (visa required for BY)

    # --- Latin America ---
    "GRU": "BR",  # Brazil (visa-free)
    "EZE": "AR",  # Argentina (visa-free)
    "MEX": "MX",  # Mexico (visa-free)
    "BOG": "CO",  # Colombia (check)
    "LIM": "PE",  # Peru (check)
    "SCL": "CL",  # Chile (check)
    "HAV": "CU",  # Cuba (visa-free)
    "PTY": "PA",  # Panama (visa-free)
}


def get_country_from_airport(iata_code: str) -> str | None:
    """Get ISO country code from IATA airport code."""
    return AIRPORT_TO_COUNTRY.get(iata_code.upper())


def get_transit_countries(route_airports: list[str]) -> list[str]:
    """Extract unique transit country codes from a list of airport codes.

    Args:
        route_airports: List of IATA codes like ["MSQ", "IST", "BKK"]

    Returns:
        Unique country codes of TRANSIT airports (all except first and last)

    Example:
        ["MSQ", "IST", "LHR", "JFK"] → ["TR", "GB"]
        (MSQ = origin, JFK = destination, IST + LHR = transit)
    """
    if len(route_airports) <= 2:
        return []  # Direct flight, no transit

    # All airports between origin and destination
    transit_airports = route_airports[1:-1]

    countries = []
    for code in transit_airports:
        country = get_country_from_airport(code)
        if country and country not in countries:
            countries.append(country)

    return countries
