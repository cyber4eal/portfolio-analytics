"""Synthetic fixtures with known structure, so the tests can assert on
relationships rather than on magic numbers."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

TICKERS = ["AAPL", "MSFT", "NVDA", "SAP.DE"]


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(20260826)


@pytest.fixture
def dates() -> pd.DatetimeIndex:
    return pd.bdate_range("2023-01-02", periods=500)


@pytest.fixture
def returns(rng: np.random.Generator, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Four holdings where MSFT is deliberately a near-clone of AAPL."""
    raw = pd.DataFrame(
        rng.normal(0.0005, 0.014, (len(dates), len(TICKERS))),
        index=dates,
        columns=TICKERS,
    )
    raw["MSFT"] = raw["AAPL"] * 0.95 + raw["MSFT"] * 0.05
    return raw


@pytest.fixture
def weights() -> pd.Series:
    return pd.Series([0.4, 0.3, 0.2, 0.1], index=TICKERS)


@pytest.fixture
def benchmark(rng: np.random.Generator, dates: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(rng.normal(0.0004, 0.010, len(dates)), index=dates, name="SPY")


@pytest.fixture
def gappy_returns(returns: pd.DataFrame) -> pd.DataFrame:
    """The multi-calendar case: the Xetra listing has no print on US holidays."""
    holed = returns.copy()
    holed.iloc[::7, holed.columns.get_loc("SAP.DE")] = np.nan
    return holed
