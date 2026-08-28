"""The comparison universe: funds this portfolio is measured against.

Two rules decide what belongs here.

  * It has to be buyable from Ireland. That means UCITS - a US-domiciled
    ETF like VTI is not on the menu no matter how good it looks, because
    PRIIPs means no KID means no retail access.
  * It has to have a Yahoo line that actually returns prices. ETFs are
    cross-listed and Yahoo's coverage is uneven per venue: EIMI.AS returns
    nothing while EIMI.L works, IUIT.AS is dead while QDVE.DE is alive.
    Every ticker below was checked against a five-year download before it
    was written down.

Where a fund has both a London (GBP) and a German or Dutch (EUR) line, the
EUR line wins. The book is priced in EUR, and mixing a GBP series into it
silently measures sterling moves as fund volatility.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Fund:
    """One comparison fund.

    `ticker` is the Yahoo line used for returns. `isin` identifies the
    share class for the KID and factsheet lookups - those are per-ISIN, not
    per-listing, so the same ISIN serves every venue the fund trades on.
    """

    id: str
    ticker: str
    isin: str
    name: str
    issuer: str
    asset: str
    currency: str
    benchmark: str

    def as_dict(self) -> dict:
        return asdict(self)


#: Popular core holdings first, then the satellites people actually chase.
UNIVERSE: tuple[Fund, ...] = (
    Fund("vwce", "VWCE.DE", "IE00BK5BQT80", "Vanguard FTSE All-World UCITS ETF (Acc)",
         "Vanguard", "Global Equity", "EUR", "FTSE All-World"),
    Fund("iwda", "IWDA.AS", "IE00B4L5Y983", "iShares Core MSCI World UCITS ETF (Acc)",
         "iShares", "Global Equity", "EUR", "MSCI World"),
    Fund("sppw", "SPPW.DE", "IE00BFY0GT14", "SPDR MSCI World UCITS ETF (Acc)",
         "SPDR", "Global Equity", "EUR", "MSCI World"),
    Fund("xdwd", "XDWD.DE", "IE00BJ0KDQ92", "Xtrackers MSCI World UCITS ETF 1C",
         "Xtrackers", "Global Equity", "EUR", "MSCI World"),
    Fund("cspx", "CSPX.AS", "IE00B5BMR087", "iShares Core S&P 500 UCITS ETF (Acc)",
         "iShares", "US Equity", "EUR", "S&P 500"),
    Fund("eqqq", "EQQQ.PA", "IE0032077012", "Invesco EQQQ Nasdaq-100 UCITS ETF",
         "Invesco", "US Equity", "EUR", "Nasdaq 100"),
    Fund("cndx", "CNDX.AS", "IE00B53SZB19", "iShares Nasdaq 100 UCITS ETF (Acc)",
         "iShares", "US Equity", "EUR", "Nasdaq 100"),
    Fund("imeu", "IMEU.AS", "IE00B1YZSC51", "iShares Core MSCI Europe UCITS ETF (Acc)",
         "iShares", "Europe Equity", "EUR", "MSCI Europe"),
    Fund("meud", "MEUD.PA", "LU0908500753", "Amundi Stoxx Europe 600 UCITS ETF (Acc)",
         "Amundi", "Europe Equity", "EUR", "STOXX Europe 600"),
    Fund("is3n", "IS3N.DE", "IE00BKM4GZ66", "iShares Core MSCI EM IMI UCITS ETF (Acc)",
         "iShares", "Emerging Markets", "EUR", "MSCI EM IMI"),
    Fund("qdve", "QDVE.DE", "IE00B3WJKG14", "iShares S&P 500 Information Technology Sector UCITS ETF",
         "iShares", "Sector Equity", "EUR", "S&P 500 Info Tech"),
    Fund("xaix", "XAIX.DE", "IE00BGV5VN51", "Xtrackers Artificial Intelligence & Big Data UCITS ETF",
         "Xtrackers", "Sector Equity", "EUR", "Nasdaq Global AI & Big Data"),
    Fund("2b76", "2B76.DE", "IE00BYZK4552", "iShares Automation & Robotics UCITS ETF",
         "iShares", "Sector Equity", "EUR", "iSTOXX FactSet Automation & Robotics"),
    Fund("iqqh", "IQQH.DE", "IE00B1XNHC34", "iShares Global Clean Energy UCITS ETF",
         "iShares", "Sector Equity", "EUR", "S&P Global Clean Energy"),
    Fund("aggh", "AGGH.AS", "IE00BDBRDM35", "iShares Core Global Aggregate Bond UCITS ETF EUR-H (Acc)",
         "iShares", "Fixed Income", "EUR", "Bloomberg Global Aggregate"),
    Fund("vagf", "VAGF.DE", "IE00BGYWT403", "Vanguard Global Aggregate Bond UCITS ETF EUR-H (Acc)",
         "Vanguard", "Fixed Income", "EUR", "Bloomberg Global Aggregate"),
    Fund("euna", "EUNA.DE", "IE00B3DKXQ41", "iShares Core Euro Government Bond UCITS ETF",
         "iShares", "Fixed Income", "EUR", "Bloomberg Euro Government"),
    Fund("sgln", "SGLN.L", "IE00B4ND3602", "iShares Physical Gold ETC",
         "iShares", "Commodity", "GBP", "LBMA Gold Price"),
)

BY_ID = {f.id: f for f in UNIVERSE}
BY_TICKER = {f.ticker: f for f in UNIVERSE}

#: The yardstick every fund and the portfolio itself is regressed against.
BENCHMARK_TICKER = "IWDA.AS"


def tickers() -> list[str]:
    return [f.ticker for f in UNIVERSE]
