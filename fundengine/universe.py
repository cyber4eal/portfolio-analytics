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
    ter: float = 0.0        # ongoing charge, annual, as a fraction

    def as_dict(self) -> dict:
        return asdict(self)


#: Ongoing charges are hand-entered from each provider's published KID and
#: are not refetched - the issuers block automated access to the pages those
#: documents live on. Verify against the current KID before acting on a
#: close comparison; a few basis points either way will not change which
#: fund wins, but it will change a total-cost figure.
#:
#: Note what these do and do not affect. A fund's price history is already
#: net of its own charge - the fee comes out of NAV daily, so every
#: historical line on this site is what a holder actually received. The TER
#: matters for the *forward* estimate, where a CAPM expected return knows
#: nothing about fees and would otherwise credit an expensive fund with the
#: same net return as a cheap one.

#: Popular core holdings first, then the satellites people actually chase.
UNIVERSE: tuple[Fund, ...] = (
    Fund("vwce", "VWCE.DE", "IE00BK5BQT80", "Vanguard FTSE All-World UCITS ETF (Acc)",
         "Vanguard", "Global Equity", "EUR", "FTSE All-World", 0.0022),
    Fund("iwda", "IWDA.AS", "IE00B4L5Y983", "iShares Core MSCI World UCITS ETF (Acc)",
         "iShares", "Global Equity", "EUR", "MSCI World", 0.002),
    Fund("sppw", "SPPW.DE", "IE00BFY0GT14", "SPDR MSCI World UCITS ETF (Acc)",
         "SPDR", "Global Equity", "EUR", "MSCI World", 0.0012),
    Fund("xdwd", "XDWD.DE", "IE00BJ0KDQ92", "Xtrackers MSCI World UCITS ETF 1C",
         "Xtrackers", "Global Equity", "EUR", "MSCI World", 0.0019),
    Fund("cspx", "CSPX.AS", "IE00B5BMR087", "iShares Core S&P 500 UCITS ETF (Acc)",
         "iShares", "US Equity", "EUR", "S&P 500", 0.0007),
    Fund("eqqq", "EQQQ.PA", "IE0032077012", "Invesco EQQQ Nasdaq-100 UCITS ETF",
         "Invesco", "US Equity", "EUR", "Nasdaq 100", 0.003),
    Fund("cndx", "CNDX.AS", "IE00B53SZB19", "iShares Nasdaq 100 UCITS ETF (Acc)",
         "iShares", "US Equity", "EUR", "Nasdaq 100", 0.0033),
    Fund("imeu", "IMEU.AS", "IE00B1YZSC51", "iShares Core MSCI Europe UCITS ETF (Acc)",
         "iShares", "Europe Equity", "EUR", "MSCI Europe", 0.0012),
    Fund("meud", "MEUD.PA", "LU0908500753", "Amundi Stoxx Europe 600 UCITS ETF (Acc)",
         "Amundi", "Europe Equity", "EUR", "STOXX Europe 600", 0.0007),
    Fund("is3n", "IS3N.DE", "IE00BKM4GZ66", "iShares Core MSCI EM IMI UCITS ETF (Acc)",
         "iShares", "Emerging Markets", "EUR", "MSCI EM IMI", 0.0018),
    Fund("qdve", "QDVE.DE", "IE00B3WJKG14", "iShares S&P 500 Information Technology Sector UCITS ETF",
         "iShares", "Sector Equity", "EUR", "S&P 500 Info Tech", 0.0015),
    Fund("xaix", "XAIX.DE", "IE00BGV5VN51", "Xtrackers Artificial Intelligence & Big Data UCITS ETF",
         "Xtrackers", "Sector Equity", "EUR", "Nasdaq Global AI & Big Data", 0.0035),
    Fund("2b76", "2B76.DE", "IE00BYZK4552", "iShares Automation & Robotics UCITS ETF",
         "iShares", "Sector Equity", "EUR", "iSTOXX FactSet Automation & Robotics", 0.004),
    Fund("iqqh", "IQQH.DE", "IE00B1XNHC34", "iShares Global Clean Energy UCITS ETF",
         "iShares", "Sector Equity", "EUR", "S&P Global Clean Energy", 0.0065),
    Fund("aggh", "AGGH.AS", "IE00BDBRDM35", "iShares Core Global Aggregate Bond UCITS ETF EUR-H (Acc)",
         "iShares", "Fixed Income", "EUR", "Bloomberg Global Aggregate", 0.001),
    Fund("vagf", "VAGF.DE", "IE00BGYWT403", "Vanguard Global Aggregate Bond UCITS ETF EUR-H (Acc)",
         "Vanguard", "Fixed Income", "EUR", "Bloomberg Global Aggregate", 0.001),
    Fund("euna", "EUNA.DE", "IE00B3DKXQ41", "iShares Core Euro Government Bond UCITS ETF",
         "iShares", "Fixed Income", "EUR", "Bloomberg Euro Government", 0.0009),
    Fund("sgln", "SGLN.L", "IE00B4ND3602", "iShares Physical Gold ETC",
         "iShares", "Commodity", "GBP", "LBMA Gold Price", 0.0012),
    # Rivals for the same exposures, so the optimiser picks a fund on its
    # cost and behaviour rather than on which issuer happened to be listed
    # here. The first pass was 11 iShares out of 18, and a shortlist that
    # lopsided produces iShares answers whatever the arithmetic says.
    Fund("vusa", "VUSA.AS", "IE00B3XXRP09", "Vanguard S&P 500 UCITS ETF (Dist)",
         "Vanguard", "US Equity", "EUR", "S&P 500", 0.0007),
    Fund("lyps", "LYPS.DE", "LU0496786574", "Amundi S&P 500 UCITS ETF (Acc)",
         "Amundi", "US Equity", "EUR", "S&P 500", 0.0005),
    Fund("spy5", "SPY5.DE", "IE00B6YX5C33", "SPDR S&P 500 UCITS ETF (Dist)",
         "SPDR", "US Equity", "EUR", "S&P 500", 0.0003),
    Fund("cw8", "CW8.PA", "LU1681043599", "Amundi MSCI World UCITS ETF (Acc)",
         "Amundi", "Global Equity", "EUR", "MSCI World", 0.0038),
    Fund("vwrl", "VWRL.AS", "IE00B3RBWM25", "Vanguard FTSE All-World UCITS ETF (Dist)",
         "Vanguard", "Global Equity", "EUR", "FTSE All-World", 0.0022),
    Fund("iusq", "IUSQ.DE", "IE00B6R52259", "iShares MSCI ACWI UCITS ETF (Acc)",
         "iShares", "Global Equity", "EUR", "MSCI ACWI", 0.0020),
    Fund("veur", "VEUR.AS", "IE00B945VV12", "Vanguard FTSE Developed Europe UCITS ETF",
         "Vanguard", "Europe Equity", "EUR", "FTSE Dev Europe", 0.0010),
    Fund("vfem", "VFEM.AS", "IE00B3VVMM84", "Vanguard FTSE Emerging Markets UCITS ETF",
         "Vanguard", "Emerging Markets", "EUR", "FTSE Emerging", 0.0022),
    Fund("aeem", "AEEM.PA", "LU1681045370", "Amundi MSCI Emerging Markets UCITS ETF",
         "Amundi", "Emerging Markets", "EUR", "MSCI EM", 0.0020),
    Fund("xdew", "XDEW.DE", "IE00BLNMYC90", "Xtrackers S&P 500 Equal Weight UCITS ETF",
         "Xtrackers", "US Equity", "EUR", "S&P 500 Equal Weight", 0.0020),
    Fund("zprv", "ZPRV.DE", "IE00BSPLC298", "SPDR MSCI USA Small Cap Value Weighted",
         "SPDR", "US Equity", "EUR", "MSCI USA Small Value", 0.0030),
    Fund("spyd", "SPYD.DE", "IE00B6YX5D40", "SPDR S&P US Dividend Aristocrats UCITS ETF",
         "SPDR", "US Equity", "EUR", "S&P High Yield Dividend Aristocrats", 0.0035),
)

BY_ID = {f.id: f for f in UNIVERSE}
BY_TICKER = {f.ticker: f for f in UNIVERSE}

#: The yardstick every fund and the portfolio itself is regressed against.
#:
#: This is the exact share class held in the book - SSGA's S&P 500 UCITS ETF
#: (Acc), Xetra line - rather than a generic index. Measuring against a line
#: you cannot buy quietly flatters or punishes you by whatever the tracking
#: difference and the currency line happen to be; measuring against the one
#: you actually own asks the only question that matters, which is whether
#: the rest of the book earned its place next to simply holding more of it.
#:
#: It carries less history than a broad index line (from 2023-11), which is
#: not a constraint here: the book's own comparable window is shorter still.
BENCHMARK_TICKER = "SPYL.DE"


def tickers() -> list[str]:
    return [f.ticker for f in UNIVERSE]


#: Ongoing charges for funds held in the book but not in the comparison
#: universe. A directly-held share carries no ongoing charge; an ETF does,
#: whether or not this project happens to compare against it. Treating every
#: holding as free would have flattered the ones that are funds.
HELD_FUND_TERS = {
    "SPYL.DE": 0.0003,   # SSGA SPDR S&P 500 UCITS ETF (Acc) - the benchmark
    "VUAA.DE": 0.0007,   # Vanguard S&P 500 UCITS ETF (Acc)
    "IEMA.AS": 0.0018,   # iShares MSCI EM UCITS ETF (Acc), Amsterdam
    "EUNM.DE": 0.0018,   # the same fund, Xetra line
    "IEMG": 0.0009,      # iShares Core MSCI EM IMI, US-listed line
    "XDEW.DE": 0.0020,   # Xtrackers S&P 500 Equal Weight
    "CSPX.AS": 0.0007,
    "IWDA.AS": 0.0020,
}


def costs_by_ticker(holdings_tickers) -> dict:
    """Annual ongoing charge per ticker: zero for shares, the TER for funds."""
    costs = {ticker: 0.0 for ticker in holdings_tickers}
    costs.update({f.ticker: f.ter for f in UNIVERSE})
    costs.update(HELD_FUND_TERS)
    return costs
