"""Per-holding descriptive data: sector, country, currency, yield.

Yahoo answers this for equities and refuses for ETFs - `sector` and
`country` come back None on every fund, because a fund does not have one of
either. So funds get a look-through table instead, written by hand from
each index's published country and sector weights.

That table is approximate and dated by construction. It is here so the map
and the sector chart cover the whole book instead of the two thirds of it
that happens to be single stocks, which would be a more misleading picture
than an approximate one clearly labelled as approximate.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / ".cache" / "profiles.json"
MAX_AGE_DAYS = 30

#: Yahoo's country strings -> the names in world-atlas countries-110m.
COUNTRY_ALIASES = {
    "United States": "United States of America",
    "USA": "United States of America",
    "South Korea": "South Korea",
    "Republic of Korea": "South Korea",
    "Hong Kong": "China",          # the atlas has no separate HK polygon
    "Czech Republic": "Czechia",
    "Russian Federation": "Russia",
    "UK": "United Kingdom",
}

#: Index weights, rounded, from each provider's published breakdown.
#: Approximate on purpose - these move every month and are not refetched.
ETF_LOOKTHROUGH: dict[str, dict] = {
    "SPYL.DE": {"index": "S&P 500", "countries": {"United States of America": 100.0},
                "sectors": {"Technology": 34.0, "Financial Services": 13.0,
                            "Consumer Cyclical": 10.5, "Healthcare": 9.5,
                            "Communication Services": 9.5, "Industrials": 8.0,
                            "Consumer Defensive": 5.5, "Energy": 3.5,
                            "Utilities": 2.5, "Real Estate": 2.0}},
    "VUAA.DE": {"index": "S&P 500", "countries": {"United States of America": 100.0},
                "sectors": {"Technology": 34.0, "Financial Services": 13.0,
                            "Consumer Cyclical": 10.5, "Healthcare": 9.5,
                            "Communication Services": 9.5, "Industrials": 8.0,
                            "Consumer Defensive": 5.5, "Energy": 3.5,
                            "Utilities": 2.5, "Real Estate": 2.0}},
    "IEMG": {"index": "MSCI EM IMI",
             "countries": {"China": 27.0, "India": 18.0, "Taiwan": 18.0,
                           "South Korea": 11.0, "Brazil": 4.0, "Saudi Arabia": 3.5,
                           "South Africa": 3.0, "Mexico": 2.5, "Indonesia": 2.0,
                           "Thailand": 1.5, "Malaysia": 1.5, "Poland": 1.0,
                           "Turkey": 1.0, "United Arab Emirates": 1.0, "Greece": 0.5,
                           "Philippines": 0.5, "Chile": 0.5, "Hungary": 0.5,
                           "Qatar": 1.0, "Egypt": 0.5, "Colombia": 0.5, "Peru": 0.5},
             "sectors": {"Technology": 27.0, "Financial Services": 22.0,
                         "Consumer Cyclical": 12.0, "Communication Services": 9.0,
                         "Industrials": 7.0, "Consumer Defensive": 5.0,
                         "Basic Materials": 6.0, "Energy": 5.0, "Healthcare": 4.0,
                         "Utilities": 2.0, "Real Estate": 1.0}},
}

#: The comparison funds, so the fund pages can show a shape too.
ETF_LOOKTHROUGH.update({
    "VWCE.DE": {"index": "FTSE All-World",
                "countries": {"United States of America": 64.0, "Japan": 6.0,
                              "United Kingdom": 3.5, "China": 3.0, "Canada": 2.7,
                              "France": 2.3, "Switzerland": 2.2, "Germany": 2.2,
                              "India": 2.0, "Taiwan": 2.2, "Australia": 1.7,
                              "South Korea": 1.2, "Netherlands": 1.2, "Sweden": 0.8,
                              "Italy": 0.7, "Spain": 0.7, "Denmark": 0.7,
                              "Brazil": 0.5, "Ireland": 0.4},
                "sectors": {"Technology": 27.0, "Financial Services": 17.0,
                            "Consumer Cyclical": 11.0, "Healthcare": 9.5,
                            "Industrials": 11.0, "Communication Services": 8.5,
                            "Consumer Defensive": 5.5, "Energy": 4.0,
                            "Basic Materials": 3.0, "Utilities": 2.5,
                            "Real Estate": 2.0}},
    "IWDA.AS": {"index": "MSCI World",
                "countries": {"United States of America": 71.0, "Japan": 5.5,
                              "United Kingdom": 3.7, "Canada": 3.0, "France": 2.6,
                              "Switzerland": 2.5, "Germany": 2.4, "Australia": 1.8,
                              "Netherlands": 1.4, "Sweden": 0.9, "Italy": 0.8,
                              "Spain": 0.8, "Denmark": 0.8, "Ireland": 0.4,
                              "Finland": 0.3, "Belgium": 0.3, "Norway": 0.3},
                "sectors": {"Technology": 28.0, "Financial Services": 17.0,
                            "Consumer Cyclical": 10.5, "Healthcare": 9.5,
                            "Industrials": 11.0, "Communication Services": 8.5,
                            "Consumer Defensive": 5.5, "Energy": 4.0,
                            "Basic Materials": 3.0, "Utilities": 2.5,
                            "Real Estate": 2.0}},
    "CSPX.AS": ETF_LOOKTHROUGH["SPYL.DE"],
    "IS3N.DE": ETF_LOOKTHROUGH["IEMG"],
    "SGLN.L": {"index": "Physical gold", "countries": {}, "sectors": {"Commodity": 100.0}},
})

#: Bonds have an issuer country but no sector in Yahoo's sense.
for ticker, index in (("AGGH.AS", "Global Aggregate"), ("VAGF.DE", "Global Aggregate"),
                      ("EUNA.DE", "Euro Government")):
    ETF_LOOKTHROUGH[ticker] = {
        "index": index,
        "countries": ({"United States of America": 40.0, "Japan": 11.0, "China": 8.0,
                       "France": 5.0, "Germany": 4.5, "United Kingdom": 4.5,
                       "Italy": 3.5, "Canada": 3.0, "Spain": 2.5}
                      if "Global" in index else
                      {"France": 24.0, "Italy": 22.0, "Germany": 19.0, "Spain": 15.0,
                       "Belgium": 5.0, "Netherlands": 5.0, "Austria": 4.0,
                       "Portugal": 3.0, "Ireland": 2.0, "Finland": 1.0}),
        "sectors": {"Fixed Income": 100.0},
    }


def _load_cache() -> dict:
    if not CACHE.exists():
        return {}
    try:
        blob = json.loads(CACHE.read_text())
    except json.JSONDecodeError:
        return {}
    if time.time() - blob.get("fetched", 0) > MAX_AGE_DAYS * 86400:
        return {}
    return blob.get("profiles", {})


def fetch(tickers: list[str], refresh: bool = False) -> dict[str, dict]:
    """Sector, country, currency and yield per ticker.

    One Yahoo call per ticker and they are slow, so the result is cached for
    a month. None of this moves fast enough to be worth refetching daily.
    """
    cached = {} if refresh else _load_cache()
    missing = [t for t in tickers if t not in cached]

    if missing:
        import yfinance as yf

        print(f"  profiling {len(missing)} ticker(s)...")
        for ticker in missing:
            record = {"sector": None, "country": None, "currency": None,
                      "yield": None, "name": None, "kind": "EQUITY"}
            try:
                info = yf.Ticker(ticker).info
                record.update(
                    sector=info.get("sector"),
                    country=info.get("country"),
                    currency=info.get("currency"),
                    name=info.get("longName") or info.get("shortName"),
                    kind=info.get("quoteType") or "EQUITY",
                )
                dividend = info.get("dividendYield")
                # Yahoo reports this as a percent, not a fraction: NVDA comes
                # back as 0.44 meaning 0.44%, AGNC as 13.16 meaning 13.16%.
                # Reading the small ones as fractions put NVDA on a 44% yield
                # and the book on 13.9%, which is roughly ten times reality.
                if dividend is not None:
                    record["yield"] = float(dividend) / 100
            except Exception as exc:                     # noqa: BLE001 - one bad
                print(f"    {ticker}: {type(exc).__name__}")  # ticker must not
            cached[ticker] = record                          # stop the build

        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps({"fetched": time.time(), "profiles": cached}))

    return {t: cached.get(t, {}) for t in tickers}


def _split(weight: float, breakdown: dict[str, float]) -> dict[str, float]:
    total = sum(breakdown.values()) or 1.0
    return {k: weight * v / total for k, v in breakdown.items()}


def exposures(holdings: list, profiles: dict[str, dict]) -> dict:
    """Country, sector and currency weights for the whole book.

    Funds are exploded through `ETF_LOOKTHROUGH` so a single ETF line does
    not sit in an "Unknown" bucket that is larger than everything else.
    `covered` says how much of the book the look-through actually reached.
    """
    priced = [h for h in holdings if h.get("tradable") and h.get("value_eur", 0) > 0]
    total = sum(h["value_eur"] for h in priced) or 1.0

    countries: dict[str, float] = {}
    sectors: dict[str, float] = {}
    currencies: dict[str, float] = {}
    looked_through = 0.0

    for holding in priced:
        ticker = holding["ticker"]
        weight = 100 * holding["value_eur"] / total
        profile = profiles.get(ticker, {})
        currencies[holding.get("currency", "USD")] = (
            currencies.get(holding.get("currency", "USD"), 0) + weight)

        table = ETF_LOOKTHROUGH.get(ticker)
        if table:
            looked_through += weight
            for country, share in _split(weight, table["countries"]).items():
                countries[country] = countries.get(country, 0) + share
            for sector, share in _split(weight, table["sectors"]).items():
                sectors[sector] = sectors.get(sector, 0) + share
            continue

        country = profile.get("country") or "Unknown"
        country = COUNTRY_ALIASES.get(country, country)
        countries[country] = countries.get(country, 0) + weight
        sector = profile.get("sector") or "Unknown"
        sectors[sector] = sectors.get(sector, 0) + weight

    return {
        "countries": dict(sorted(countries.items(), key=lambda kv: -kv[1])),
        "sectors": dict(sorted(sectors.items(), key=lambda kv: -kv[1])),
        "currencies": dict(sorted(currencies.items(), key=lambda kv: -kv[1])),
        "lookThroughWeight": round(looked_through, 2),
    }


def income(holdings: list, profiles: dict[str, dict]) -> dict:
    """Projected annual dividend income at today's yields."""
    lines = []
    total = 0.0
    for holding in holdings:
        if not holding.get("tradable"):
            continue
        rate = profiles.get(holding["ticker"], {}).get("yield")
        if not rate:
            continue
        amount = holding["value_eur"] * rate
        total += amount
        lines.append({"ticker": holding["ticker"], "name": holding["name"],
                      "yield": round(rate * 100, 2), "annual_eur": round(amount, 2)})
    lines.sort(key=lambda d: -d["annual_eur"])
    invested = sum(h["value_eur"] for h in holdings if h.get("tradable")) or 1.0
    return {"annual_eur": round(total, 2),
            "portfolio_yield": round(100 * total / invested, 2),
            "top": lines[:8]}
