"""
Realistic reference pools — coherent geography, routing numbers, names.

The mock's original generators drew City, State, ZipCode and area code
independently from Faker, which produces values that never agree with each
other (`PORT TIMOTHY, UT 61685`, `TX 05082`, area code `199`). Real bureau data
is internally consistent: a ZIP belongs to its state, an area code serves its
metro, an SSN is issued after the holder is born.

This module is the single source of coherent values. Pick one market with
`resolve_market()` and derive every geographic field from it.

Pure data + pure functions, no I/O, so both the data pipeline and
`response_generator` can import it without side effects.

Scope: a 28-state US footprint centred on the Mid-Atlantic and Midwest, plus
southern and western metros. Markets are weighted rather than uniform, so the
corpus concentrates the way a real regional bank's book does instead of
spreading evenly across the country.
"""

from __future__ import annotations

import hashlib
import random
from typing import Dict, List, NamedTuple, Optional, Sequence

# ── Routing numbers ─────────────────────────────────────────────────────────
# Real ABA routing numbers, keyed by state, taken from one large US bank's
# published listings so that a routing number and a state actually agree.
#
# NOTE: five states (TX, AZ, CA, CO, NM) are deliberately absent — no regional
# ABA for them appeared in the verified source, and inventing one is exactly
# the detail a reviewer would catch. They fall back to the national number via
# `routing_number_for()`. Confirm the real regional ABAs with the sponsoring
# institution before relying on those rows.
ROUTING_NUMBERS: Dict[str, str] = {
    "PA": "043000096",
    "AL": "043000096",
    "IN": "043000096",
    "NC": "043000096",
    "DC": "054000030",
    "MD": "054000030",
    "VA": "054000030",
    "DE": "031100089",
    "FL": "267084199",
    "GA": "061192630",
    "SC": "053100850",
    "IL": "071921891",
    "MO": "071921891",
    "WI": "071921891",
    "KY": "083000108",
    "MI": "041000124",
    "OH": "042000398",
    "NJ": "031207607",
    "WV": "271971560",
}

#: The national / incoming-wire routing number, and the fallback for states
#: with no verified regional ABA.
NATIONAL_ROUTING = "043000096"


def routing_number_for(state: str) -> str:
    """The routing number serving `state`, falling back to the national ABA."""
    return ROUTING_NUMBERS.get(state.upper(), NATIONAL_ROUTING)


# ── Markets ─────────────────────────────────────────────────────────────────

class Market(NamedTuple):
    """One coherent metro: city, state, ZIP range and the area codes serving it."""

    city: str
    state: str
    zip_prefix: str      # first 3 digits; the last 2 are randomised
    area_codes: Sequence[str]
    weight: int          # relative share of the branch/customer footprint


#: Real US metros. `weight` approximates a regional branch distribution:
#: Pennsylvania and Ohio form the historic core, Texas is the largest single
#: state outside it.
MARKETS: List[Market] = [
    # ── Pennsylvania — headquarters state, heaviest weight ──
    Market("PITTSBURGH", "PA", "152", ("412", "724", "878"), 100),
    Market("PHILADELPHIA", "PA", "191", ("215", "267", "445"), 90),
    Market("ALLENTOWN", "PA", "181", ("610", "484"), 30),
    Market("HARRISBURG", "PA", "171", ("717", "223"), 25),
    Market("SCRANTON", "PA", "185", ("570", "272"), 20),
    Market("ERIE", "PA", "165", ("814",), 18),
    # ── Ohio ──
    Market("CLEVELAND", "OH", "441", ("216", "440"), 70),
    Market("COLUMBUS", "OH", "432", ("614", "380"), 65),
    Market("CINCINNATI", "OH", "452", ("513", "283"), 55),
    Market("AKRON", "OH", "443", ("330", "234"), 30),
    Market("TOLEDO", "OH", "436", ("419", "567"), 25),
    Market("DAYTON", "OH", "454", ("937", "326"), 22),
    # ── New Jersey / Delaware ──
    Market("CHERRY HILL", "NJ", "080", ("856",), 35),
    Market("NEWARK", "NJ", "071", ("973", "862"), 30),
    Market("TRENTON", "NJ", "086", ("609", "640"), 22),
    Market("JERSEY CITY", "NJ", "073", ("201", "551"), 20),
    Market("WILMINGTON", "DE", "198", ("302",), 30),
    # ── Maryland / DC / Virginia ──
    Market("BALTIMORE", "MD", "212", ("410", "443", "667"), 50),
    Market("ROCKVILLE", "MD", "208", ("301", "240"), 28),
    Market("WASHINGTON", "DC", "200", ("202",), 35),
    Market("ARLINGTON", "VA", "222", ("703", "571"), 30),
    Market("RICHMOND", "VA", "232", ("804",), 25),
    # ── Indiana / Kentucky ──
    Market("INDIANAPOLIS", "IN", "462", ("317", "463"), 45),
    Market("FORT WAYNE", "IN", "468", ("260",), 20),
    Market("EVANSVILLE", "IN", "477", ("812",), 15),
    Market("LOUISVILLE", "KY", "402", ("502",), 35),
    Market("LEXINGTON", "KY", "405", ("859",), 22),
    # ── Illinois / Wisconsin / Missouri ──
    Market("CHICAGO", "IL", "606", ("312", "773", "872"), 60),
    Market("NAPERVILLE", "IL", "605", ("630", "331"), 25),
    Market("MILWAUKEE", "WI", "532", ("414",), 30),
    Market("MADISON", "WI", "537", ("608",), 18),
    Market("SAINT LOUIS", "MO", "631", ("314",), 30),
    Market("KANSAS CITY", "MO", "641", ("816",), 25),
    # ── Michigan ──
    Market("DETROIT", "MI", "482", ("313",), 40),
    Market("GRAND RAPIDS", "MI", "495", ("616",), 25),
    Market("ANN ARBOR", "MI", "481", ("734",), 20),
    # ── Carolinas / Georgia ──
    Market("CHARLOTTE", "NC", "282", ("704", "980"), 45),
    Market("RALEIGH", "NC", "276", ("919", "984"), 35),
    Market("GREENSBORO", "NC", "274", ("336",), 20),
    Market("ATLANTA", "GA", "303", ("404", "470", "678"), 45),
    Market("SAVANNAH", "GA", "314", ("912",), 15),
    Market("CHARLESTON", "SC", "294", ("843",), 20),
    Market("COLUMBIA", "SC", "292", ("803",), 18),
    # ── Florida ──
    Market("TAMPA", "FL", "336", ("813",), 40),
    Market("ORLANDO", "FL", "328", ("407", "689"), 35),
    Market("MIAMI", "FL", "331", ("305", "786"), 35),
    Market("JACKSONVILLE", "FL", "322", ("904",), 25),
    Market("NAPLES", "FL", "341", ("239",), 15),
    # ── West Virginia ──
    Market("CHARLESTON", "WV", "253", ("304",), 15),
    Market("MORGANTOWN", "WV", "265", ("304",), 12),
    # ── Alabama ──
    Market("BIRMINGHAM", "AL", "352", ("205",), 40),
    Market("HUNTSVILLE", "AL", "358", ("256",), 22),
    Market("MONTGOMERY", "AL", "361", ("334",), 18),
    Market("MOBILE", "AL", "366", ("251",), 16),
    # ── Texas — largest single state outside the core ──
    Market("DALLAS", "TX", "752", ("214", "469", "972"), 70),
    Market("HOUSTON", "TX", "770", ("713", "281", "832"), 70),
    Market("AUSTIN", "TX", "787", ("512", "737"), 45),
    Market("SAN ANTONIO", "TX", "782", ("210", "726"), 40),
    Market("FORT WORTH", "TX", "761", ("817", "682"), 35),
    Market("EL PASO", "TX", "799", ("915",), 20),
    # ── Arizona / Colorado / New Mexico / California ──
    Market("PHOENIX", "AZ", "850", ("602", "623", "480"), 40),
    Market("TUCSON", "AZ", "857", ("520",), 18),
    Market("DENVER", "CO", "802", ("303", "720"), 35),
    Market("COLORADO SPRINGS", "CO", "809", ("719",), 18),
    Market("ALBUQUERQUE", "NM", "871", ("505",), 20),
    Market("LOS ANGELES", "CA", "900", ("213", "323"), 35),
    Market("SAN DIEGO", "CA", "921", ("619", "858"), 25),
    Market("SAN FRANCISCO", "CA", "941", ("415", "628"), 25),
    Market("SACRAMENTO", "CA", "958", ("916",), 20),
]

_MARKET_WEIGHTS = [m.weight for m in MARKETS]

#: Every state in the footprint, derived from the market list.
STATES: List[str] = sorted({m.state for m in MARKETS})


def resolve_market(
    state: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> Market:
    """
    Pick one coherent market, weighted by the footprint.

    Every geographic field downstream (city, state, ZIP, area code, and the
    routing number) should be derived from the single Market returned here —
    that is what keeps them agreeing with one another.

    `state` restricts the draw to that state; unknown states fall back to the
    full weighted pool rather than raising, so callers can pass through
    user-supplied values safely.
    """
    rnd = rng or random
    if state:
        candidates = [m for m in MARKETS if m.state == state.upper()]
        if candidates:
            weights = [m.weight for m in candidates]
            return rnd.choices(candidates, weights=weights, k=1)[0]
    return rnd.choices(MARKETS, weights=_MARKET_WEIGHTS, k=1)[0]


def zip_for(market: Market, rng: Optional[random.Random] = None) -> str:
    """A 5-digit ZIP inside `market`'s real 3-digit prefix range."""
    rnd = rng or random
    return f"{market.zip_prefix}{rnd.randint(0, 99):02d}"


# ── Phone numbers ───────────────────────────────────────────────────────────
# NANP validity: area code and exchange both start 2-9, and neither may be an
# N11 service code (211, 311, ... 911). The original `_phone_parts()` used
# randint(100, 999) for both, which emits invalid numbers like 199-637-5277.

_N11 = frozenset({"211", "311", "411", "511", "611", "711", "811", "911"})


def _valid_exchange(rng: random.Random) -> str:
    """A NANP-valid 3-digit exchange (NXX: leading 2-9, not N11)."""
    while True:
        exch = f"{rng.randint(200, 999)}"
        if exch not in _N11:
            return exch


def phone_parts_for(
    market: Optional[Market] = None,
    rng: Optional[random.Random] = None,
) -> Dict[str, str]:
    """
    NANP-valid phone components, using an area code that really serves
    `market`. Falls back to a random footprint area code when no market given.
    """
    rnd = rng or random
    mkt = market or resolve_market(rng=rnd)
    area = rnd.choice(list(mkt.area_codes))
    exch = _valid_exchange(rnd)
    sfx = f"{rnd.randint(0, 9999):04d}"
    return {
        "AreaCode": area,
        "Exchange": exch,
        "Suffix": sfx,
        "PhoneNumber": f"{area}{exch}{sfx}",
    }


# ── Street addresses ────────────────────────────────────────────────────────
# The original used `fake.last_name()` as the street name, giving streets like
# "CHAN EXPY" and "JIMENEZ RD". Real US street names skew heavily to trees,
# presidents, numbers and civic names.

STREET_NAMES: List[str] = [
    # Trees / nature — the single most common US street-name family
    "OAK", "MAPLE", "PINE", "CEDAR", "WALNUT", "CHESTNUT", "WILLOW", "BIRCH",
    "SPRUCE", "ELM", "LAUREL", "MAGNOLIA", "DOGWOOD", "SYCAMORE", "HICKORY",
    "MEADOW", "RIDGE", "CREEK", "RIVER", "LAKE", "FOREST", "VALLEY", "HILL",
    "SPRING", "SUNSET", "SUNRISE", "HIGHLAND", "FAIRVIEW", "RIVERSIDE",
    # Presidents / statesmen
    "WASHINGTON", "JEFFERSON", "LINCOLN", "MADISON", "MONROE", "JACKSON",
    "ADAMS", "FRANKLIN", "HAMILTON", "GRANT", "WILSON", "ROOSEVELT",
    # Civic / directional
    "MAIN", "MARKET", "CHURCH", "SCHOOL", "COLLEGE", "UNION", "LIBERTY",
    "CENTER", "CENTRAL", "BROAD", "HIGH", "MILL", "PARK", "STATE", "COURT",
    "COMMERCE", "INDUSTRIAL", "PENN", "CANAL", "DEPOT",
    # Numbered streets
    "1ST", "2ND", "3RD", "4TH", "5TH", "6TH", "7TH", "8TH", "9TH", "10TH",
    "11TH", "12TH", "15TH", "20TH",
]

#: Weighted so most addresses are plain streets/roads, as in reality.
STREET_SUFFIXES: List[str] = [
    "ST", "ST", "ST", "ST", "RD", "RD", "RD", "AVE", "AVE", "AVE",
    "DR", "DR", "LN", "LN", "BLVD", "CT", "WAY", "PL", "CIR", "PKWY", "TER",
]

#: Most US addresses carry no directional at all.
DIRECTIONALS: List[str] = [""] * 12 + ["N", "S", "E", "W", "NE", "NW", "SE", "SW"]

#: Most are single-family; unit types appear on the minority.
UNIT_TYPES: List[str] = [""] * 6 + ["APT", "APT", "STE", "UNIT", "#"]


# ── Bank names ──────────────────────────────────────────────────────────────
# Replaces `f"{fake.last_name().upper()} NATIONAL BANK"`, which produced
# "BIRD NATIONAL BANK". These are real US institutions a commercial payee
# would plausibly bank with, drawn uniformly — the pool models no sponsoring
# institution, so there is no on-us concentration to weight toward.

BANK_NAMES: List[str] = [
    "JPMORGAN CHASE BANK NA", "BANK OF AMERICA NA", "WELLS FARGO BANK NA",
    "CITIBANK NA", "TRUIST BANK", "US BANK NA", "FIFTH THIRD BANK NA",
    "KEYBANK NA", "HUNTINGTON NATIONAL BANK", "M&T BANK", "CITIZENS BANK NA",
    "REGIONS BANK", "FIRST HORIZON BANK", "SANTANDER BANK NA",
    "TD BANK NA", "CAPITAL ONE NA", "COMERICA BANK", "ZIONS BANCORPORATION",
    "FIRST NATIONAL BANK OF PENNSYLVANIA", "DOLLAR BANK FSB",
    "NORTHWEST BANK", "S&T BANK", "WESBANCO BANK INC",
]

#: Routing numbers whose institution is documented somewhere the corpus must
#: agree with; everything else gets a stable hash-draw from BANK_NAMES.
BANK_NAMES_BY_ROUTING: Dict[str, str] = {
    # GIACT sandbox routing (docs/references/sandbox-test-data.md) — must
    # agree with request_generator.SANDBOX_BANK_NAME.
    "122105278": "WELLS FARGO BANK NA (ARIZONA)",
    # The demo account routing used across the committed samples: Bank of
    # America's Texas ABA, matching the sample identities' TX geography.
    "111000025": "BANK OF AMERICA NA",
}


def bank_name_for(routing_number: Optional[str]) -> str:
    """The institution behind a routing number — a pure function of the
    routing, so the same routing never surfaces under two different bank
    names across responses, vendors, or runs. Pinned routings return their
    documented institution; anything else indexes into BANK_NAMES via
    hashlib (builtin hash() is salted per process and would not be stable).
    """
    routing = (routing_number or "").strip()
    pinned = BANK_NAMES_BY_ROUTING.get(routing)
    if pinned:
        return pinned
    digest = hashlib.sha256(routing.encode("utf-8")).digest()
    return BANK_NAMES[int.from_bytes(digest[:4], "big") % len(BANK_NAMES)]


# ── Person names ────────────────────────────────────────────────────────────
# Faker's en_US name pools are fine for realism; what was missing is the
# link between DOB and SSN issue year. `ssn_issue_years_for()` enforces it.

def ssn_issue_years_for(
    birth_year: int,
    rng: Optional[random.Random] = None,
) -> Dict[str, str]:
    """
    SSN issue window consistent with a birth year.

    The original drew `SsnIssueStartYear` from randint(1960, 2005) with no
    reference to DateOfBirth, producing records born in 2005 whose SSN was
    issued in 1973. Since the 1987 "Enumeration at Birth" programme most SSNs
    are issued in the first year of life; before that, typically at first
    employment.
    """
    rnd = rng or random
    if birth_year >= 1990:
        offset = rnd.choices([0, 1, 2], weights=[80, 15, 5], k=1)[0]
    elif birth_year >= 1975:
        offset = rnd.choices([0, 1, 5, 14, 16], weights=[45, 15, 10, 15, 15], k=1)[0]
    else:
        offset = rnd.choices([0, 14, 16, 18, 22], weights=[15, 25, 25, 20, 15], k=1)[0]
    start = birth_year + offset
    return {
        "SsnIssueStartYear": str(start),
        "SsnIssueEndYear": str(start + rnd.randint(0, 2)),
    }


#: SSN status weighted the way a real payee book looks — overwhelmingly clear.
#: The original gave "deceased" a flat 1-in-4 chance, which is absurd for a
#: population of active payees.
SSN_STATUSES: List[str] = (
    ["clear"] * 92 + ["issued-recently"] * 5 + ["suspicious"] * 2 + ["deceased"]
)


# ── Businesses ──────────────────────────────────────────────────────────────
# `fake.company()` yields "Smith-Johnson" style names. Commercial payees look
# more like "ALLEGHENY MECHANICAL CONTRACTORS LLC" — a place or family name,
# a line of business, and an entity suffix that agrees with CorporationType.

BUSINESS_PREFIXES: List[str] = [
    "ALLEGHENY", "KEYSTONE", "LIBERTY", "SUMMIT", "PIONEER", "HERITAGE",
    "CORNERSTONE", "MERIDIAN", "PINNACLE", "LANDMARK", "PREMIER", "APEX",
    "TRI-STATE", "MIDWEST", "ATLANTIC", "GATEWAY", "RIVERFRONT", "NORTHSIDE",
    "SOUTHPOINT", "EASTGATE", "WESTFIELD", "BLUE RIDGE", "GREAT LAKES",
    "LONE STAR", "GULF COAST", "RED ROCK", "FRONT RANGE", "BAY AREA",
]

BUSINESS_CORES: List[str] = [
    "MECHANICAL", "ELECTRICAL", "PLUMBING", "ROOFING", "PAVING", "CONCRETE",
    "LOGISTICS", "FREIGHT", "TRUCKING", "DISTRIBUTION", "SUPPLY", "EQUIPMENT",
    "MEDICAL", "DENTAL", "VETERINARY", "HOME HEALTH", "REHABILITATION",
    "STAFFING", "PAYROLL", "ACCOUNTING", "TAX", "LEGAL", "TITLE", "ESCROW",
    "PROPERTY", "REALTY", "DEVELOPMENT", "PROPERTY MANAGEMENT",
    "FOOD SERVICE", "CATERING", "HOSPITALITY", "FACILITIES", "JANITORIAL",
    "LANDSCAPING", "SECURITY", "TECHNOLOGY", "SOFTWARE", "DATA SYSTEMS",
    "CONSULTING", "MARKETING", "PRINTING", "PACKAGING", "MANUFACTURING",
]

BUSINESS_TAILS: List[str] = [
    "SERVICES", "SOLUTIONS", "GROUP", "PARTNERS", "ASSOCIATES", "CONTRACTORS",
    "ENTERPRISES", "INDUSTRIES", "SYSTEMS", "HOLDINGS", "COMPANY", "",
]

#: Entity suffix must agree with the record's CorporationType — a business
#: registered as an LLC does not call itself "INC".
CORP_TYPE_SUFFIXES: Dict[str, List[str]] = {
    "CORPORATION": ["INC", "INC", "CORP", ""],
    "LLC": ["LLC", "LLC", "L.L.C.", ""],
    "PARTNERSHIP": ["LP", "LLP", "PARTNERS", ""],
    "SOLE PROPRIETORSHIP": ["", "", "CO"],
}

#: CorporationType -> the RegistrationType a state filing would show.
CORP_TYPE_REGISTRATIONS: Dict[str, str] = {
    "CORPORATION": "DOMESTIC CORPORATION",
    "LLC": "LIMITED LIABILITY COMPANY",
    "PARTNERSHIP": "LIMITED PARTNERSHIP",
    "SOLE PROPRIETORSHIP": "DOMESTIC CORPORATION",
}

#: Line of business, aligned with the mock's existing INDUSTRIES pool.
BUSINESS_CORE_INDUSTRY: Dict[str, str] = {
    "MECHANICAL": "CONSTRUCTION", "ELECTRICAL": "CONSTRUCTION",
    "PLUMBING": "CONSTRUCTION", "ROOFING": "CONSTRUCTION",
    "PAVING": "CONSTRUCTION", "CONCRETE": "CONSTRUCTION",
    "LOGISTICS": "TRANSPORTATION", "FREIGHT": "TRANSPORTATION",
    "TRUCKING": "TRANSPORTATION", "DISTRIBUTION": "TRANSPORTATION",
    "SUPPLY": "RETAIL TRADE", "EQUIPMENT": "RETAIL TRADE",
    "MEDICAL": "HEALTH CARE SERVICES", "DENTAL": "HEALTH CARE SERVICES",
    "VETERINARY": "HEALTH CARE SERVICES", "HOME HEALTH": "HEALTH CARE SERVICES",
    "REHABILITATION": "HEALTH CARE SERVICES",
    "STAFFING": "ACCOUNTING SERVICES", "PAYROLL": "ACCOUNTING SERVICES",
    "ACCOUNTING": "ACCOUNTING SERVICES", "TAX": "ACCOUNTING SERVICES",
    "LEGAL": "LEGAL SERVICES", "TITLE": "LEGAL SERVICES",
    "ESCROW": "LEGAL SERVICES",
    "PROPERTY": "REAL ESTATE", "REALTY": "REAL ESTATE",
    "DEVELOPMENT": "REAL ESTATE", "PROPERTY MANAGEMENT": "REAL ESTATE",
    "FOOD SERVICE": "FOOD SERVICES", "CATERING": "FOOD SERVICES",
    "HOSPITALITY": "FOOD SERVICES",
    "FACILITIES": "CONSTRUCTION", "JANITORIAL": "CONSTRUCTION",
    "LANDSCAPING": "CONSTRUCTION", "SECURITY": "CONSTRUCTION",
    "TECHNOLOGY": "SOFTWARE DEVELOPMENT", "SOFTWARE": "SOFTWARE DEVELOPMENT",
    "DATA SYSTEMS": "SOFTWARE DEVELOPMENT",
    "CONSULTING": "MARKETING CONSULTING", "MARKETING": "MARKETING CONSULTING",
    "PRINTING": "RETAIL TRADE", "PACKAGING": "RETAIL TRADE",
    "MANUFACTURING": "RETAIL TRADE",
}


def business_name_for(
    corp_type: str,
    rng: Optional[random.Random] = None,
) -> Dict[str, str]:
    """
    A commercial-payee business name whose entity suffix agrees with
    `corp_type`. Returns the name plus the industry implied by its core term,
    so BusinessName / CorporationType / Industry stay mutually consistent.
    """
    rnd = rng or random
    prefix = rnd.choice(BUSINESS_PREFIXES)
    core = rnd.choice(BUSINESS_CORES)
    tail = rnd.choice(BUSINESS_TAILS)
    suffix = rnd.choice(CORP_TYPE_SUFFIXES.get(corp_type, [""]))
    parts = [p for p in (prefix, core, tail, suffix) if p]
    return {
        "BusinessName": " ".join(parts),
        "Industry": BUSINESS_CORE_INDUSTRY.get(core, "RETAIL TRADE"),
        "RegistrationType": CORP_TYPE_REGISTRATIONS.get(
            corp_type, "DOMESTIC CORPORATION"
        ),
    }


# ── Geo coordinates ─────────────────────────────────────────────────────────
# For IpAddressInformationResult, whose Latitude/Longitude were previously
# drawn globally at random while City/State came from elsewhere entirely.

#: Approximate metro-centre coordinates, keyed by "CITY, ST".
MARKET_COORDS: Dict[str, tuple] = {
    "PITTSBURGH, PA": (40.4406, -79.9959),
    "PHILADELPHIA, PA": (39.9526, -75.1652),
    "ALLENTOWN, PA": (40.6084, -75.4902),
    "HARRISBURG, PA": (40.2732, -76.8867),
    "SCRANTON, PA": (41.4090, -75.6624),
    "ERIE, PA": (42.1292, -80.0851),
    "CLEVELAND, OH": (41.4993, -81.6944),
    "COLUMBUS, OH": (39.9612, -82.9988),
    "CINCINNATI, OH": (39.1031, -84.5120),
    "AKRON, OH": (41.0814, -81.5190),
    "TOLEDO, OH": (41.6528, -83.5379),
    "DAYTON, OH": (39.7589, -84.1916),
    "CHERRY HILL, NJ": (39.9348, -75.0307),
    "NEWARK, NJ": (40.7357, -74.1724),
    "TRENTON, NJ": (40.2171, -74.7429),
    "JERSEY CITY, NJ": (40.7178, -74.0431),
    "WILMINGTON, DE": (39.7391, -75.5398),
    "BALTIMORE, MD": (39.2904, -76.6122),
    "ROCKVILLE, MD": (39.0840, -77.1528),
    "WASHINGTON, DC": (38.9072, -77.0369),
    "ARLINGTON, VA": (38.8816, -77.0910),
    "RICHMOND, VA": (37.5407, -77.4360),
    "INDIANAPOLIS, IN": (39.7684, -86.1581),
    "FORT WAYNE, IN": (41.0793, -85.1394),
    "EVANSVILLE, IN": (37.9716, -87.5711),
    "LOUISVILLE, KY": (38.2527, -85.7585),
    "LEXINGTON, KY": (38.0406, -84.5037),
    "CHICAGO, IL": (41.8781, -87.6298),
    "NAPERVILLE, IL": (41.7508, -88.1535),
    "MILWAUKEE, WI": (43.0389, -87.9065),
    "MADISON, WI": (43.0731, -89.4012),
    "SAINT LOUIS, MO": (38.6270, -90.1994),
    "KANSAS CITY, MO": (39.0997, -94.5786),
    "DETROIT, MI": (42.3314, -83.0458),
    "GRAND RAPIDS, MI": (42.9634, -85.6681),
    "ANN ARBOR, MI": (42.2808, -83.7430),
    "CHARLOTTE, NC": (35.2271, -80.8431),
    "RALEIGH, NC": (35.7796, -78.6382),
    "GREENSBORO, NC": (36.0726, -79.7920),
    "ATLANTA, GA": (33.7490, -84.3880),
    "SAVANNAH, GA": (32.0809, -81.0912),
    "CHARLESTON, SC": (32.7765, -79.9311),
    "COLUMBIA, SC": (34.0007, -81.0348),
    "TAMPA, FL": (27.9506, -82.4572),
    "ORLANDO, FL": (28.5383, -81.3792),
    "MIAMI, FL": (25.7617, -80.1918),
    "JACKSONVILLE, FL": (30.3322, -81.6557),
    "NAPLES, FL": (26.1420, -81.7948),
    "CHARLESTON, WV": (38.3498, -81.6326),
    "MORGANTOWN, WV": (39.6295, -79.9559),
    "BIRMINGHAM, AL": (33.5186, -86.8104),
    "HUNTSVILLE, AL": (34.7304, -86.5861),
    "MONTGOMERY, AL": (32.3668, -86.3000),
    "MOBILE, AL": (30.6954, -88.0399),
    "DALLAS, TX": (32.7767, -96.7970),
    "HOUSTON, TX": (29.7604, -95.3698),
    "AUSTIN, TX": (30.2672, -97.7431),
    "SAN ANTONIO, TX": (29.4241, -98.4936),
    "FORT WORTH, TX": (32.7555, -97.3308),
    "EL PASO, TX": (31.7619, -106.4850),
    "PHOENIX, AZ": (33.4484, -112.0740),
    "TUCSON, AZ": (32.2226, -110.9747),
    "DENVER, CO": (39.7392, -104.9903),
    "COLORADO SPRINGS, CO": (38.8339, -104.8214),
    "ALBUQUERQUE, NM": (35.0844, -106.6504),
    "LOS ANGELES, CA": (34.0522, -118.2437),
    "SAN DIEGO, CA": (32.7157, -117.1611),
    "SAN FRANCISCO, CA": (37.7749, -122.4194),
    "SACRAMENTO, CA": (38.5816, -121.4944),
}


def coords_for(
    market: Market,
    rng: Optional[random.Random] = None,
) -> Dict[str, float]:
    """Lat/long near `market`'s centre, jittered to look like a real geo-IP fix."""
    rnd = rng or random
    lat, lon = MARKET_COORDS.get(f"{market.city}, {market.state}", (39.8283, -98.5795))
    return {
        "Latitude": round(lat + rnd.uniform(-0.25, 0.25), 4),
        "Longitude": round(lon + rnd.uniform(-0.25, 0.25), 4),
    }
