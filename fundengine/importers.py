"""Turn broker statements into ledger trades.

The sheet records what you hold now. These files record how you got there,
which is the half that makes cost basis, realised P&L and a true performance
series possible.

Three formats were offered and two are usable:

  * Trading 212 CSV - a clean transaction export, fractional shares, prices
    in the instrument's currency with an FX rate alongside.
  * Davy .xls - an OLE2 workbook, one row per contract note, prices in the
    instrument's currency and the debit in EUR.
  * Trading 212 PDF - unusable. Its embedded font carries no ToUnicode map
    for digits, so every number extracts as a null byte; the customer name
    comes out as "C t lin Bond ri". Nothing can be recovered from it, and it
    covers the same account as the CSV anyway.

Symbols are mapped explicitly. A broker's ticker is not always a Yahoo line
and is not always the line the sheet uses - guessing silently is how a trade
ends up booked against the wrong instrument.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .ledger import BUY, SELL, Trade

#: Broker symbol -> the ticker this project uses. Anything absent is
#: reported rather than guessed.
SYMBOL_MAP = {
    # Trading 212
    "NVDA": "NVDA", "TSLA": "TSLA", "AMZN": "AMZN", "AGNC": "AGNC",
    "ARR": "ARR", "XOM": "XOM", "APLD": "APLD", "NNE": "NNE",
    "AMD": "AMD", "MSTR": "MSTR", "RR": "RR", "HUMA": "HUMA",
    "AIR1": "EADSY",     # Airbus: T212's European line, the sheet holds the ADR
    "EUNM": "IEMG",      # iShares MSCI EM: the sheet holds the US-listed line
    "GOOGL": "GOOGL", "ANET": "ANET", "AVGO": "AVGO",
    "VUAA": "VUAA.DE",   # Vanguard S&P 500 Acc, Xetra line
    "RHM": "RHM.DE",     # Rheinmetall, Xetra
    # Davy contract-note descriptions, matched on a distinctive fragment
    "TESLA INC": "TSLA",
    "NVIDIA CORP": "NVDA",
    "PALANTIR TECH": "PLTR",
    "AMAZON COM INC": "AMZN",
    "BYD COMPANY": "BYDDY",
    "BROADCOM INC": "AVGO",
    "ADVANCED MICRO DEV": "AMD",
    "XTRACKERS S&P500 EQL WGHT": "XDEW.DE",
}

#: Rows that move cash rather than instruments.
CASH_TYPES = {"CASH TOP-UP", "CASH WITHDRAWAL", "DIVIDEND", "INTEREST",
              "LENDING INTEREST", "CARD DEBIT", "CARD CREDIT"}


@dataclass
class ImportResult:
    trades: list
    skipped: list
    unmapped: list
    source: str

    def summary(self) -> str:
        lines = [f"{self.source}: {len(self.trades)} trade(s)"]
        if self.unmapped:
            lines.append(f"  unmapped symbols (not imported): {', '.join(sorted(set(self.unmapped)))}")
        if self.skipped:
            kinds = {}
            for reason in self.skipped:
                kinds[reason] = kinds.get(reason, 0) + 1
            lines.append("  skipped: " + ", ".join(f"{n} x {k}" for k, n in sorted(kinds.items())))
        return "\n".join(lines)


def _money(text: str) -> float:
    """'USD 196.39' or '-1,102.70' -> a number."""
    cleaned = re.sub(r"[A-Za-z€$£,\s]", "", str(text or ""))
    if not cleaned or cleaned in ("-", "."):
        return 0.0
    return float(cleaned)


def _currency(text: str) -> str:
    match = re.match(r"\s*([A-Z]{3})\s", str(text or ""))
    return match.group(1) if match else "EUR"


def _map_symbol(raw: str) -> str | None:
    key = raw.strip().upper()
    if key in SYMBOL_MAP:
        return SYMBOL_MAP[key]
    for fragment, ticker in SYMBOL_MAP.items():
        if len(fragment) > 6 and fragment in key:
            return ticker
    return None


def trading212_csv(path: str | Path, portfolio: str) -> ImportResult:
    """Parse a Trading 212 transaction export."""
    trades, skipped, unmapped = [], [], []

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            kind = (row.get("Type") or "").strip().upper()
            symbol = (row.get("Ticker") or "").strip()

            if not symbol or kind in CASH_TYPES:
                skipped.append(kind or "cash movement")
                continue
            if "BUY" not in kind and "SELL" not in kind:
                skipped.append(kind)
                continue

            ticker = _map_symbol(symbol)
            if not ticker:
                unmapped.append(symbol)
                continue

            shares = float(row.get("Quantity") or 0)
            if shares <= 0:
                skipped.append("zero quantity")
                continue

            price_field = row.get("Price per share") or ""
            trades.append(Trade(
                ticker=ticker,
                action=BUY if "BUY" in kind else SELL,
                shares=shares,
                price=_money(price_field),
                currency=_currency(price_field) or (row.get("Currency") or "EUR").strip(),
                date=(row.get("Date") or "")[:10],
                portfolio=portfolio,
                note=f"Trading 212 {kind.lower()}",
            ))

    return ImportResult(trades, skipped, unmapped, "Trading 212 CSV")


def davy_xls(path: str | Path, portfolio: str) -> ImportResult:
    """Parse a Davy transaction statement.

    Column order is Contract Ref, Date, Type, Quantity, Price, Description,
    Debit/Credit, Balance - but cash rows have no contract reference and are
    shifted one column left, so rows are matched on where the date is rather
    than on a fixed offset.
    """
    import pandas as pd

    frame = pd.read_excel(path, header=None)
    trades, skipped, unmapped = [], [], []

    for _, row in frame.iterrows():
        cells = ["" if pd.isna(c) else str(c).strip() for c in row.tolist()]
        joined = " ".join(cells).upper()
        if "BOUGHT" not in joined and "SOLD" not in joined:
            if any(cells):
                skipped.append("non-trade row")
            continue

        try:
            kind_index = next(i for i, c in enumerate(cells)
                              if c.upper() in ("BOUGHT", "SOLD"))
        except StopIteration:
            skipped.append("no direction")
            continue

        date_cell = cells[kind_index - 1] if kind_index else ""
        try:
            when = datetime.strptime(date_cell, "%d/%m/%Y").date().isoformat()
        except ValueError:
            skipped.append("unparsed date")
            continue

        try:
            shares = float(cells[kind_index + 1])
            price = float(cells[kind_index + 2])
        except (ValueError, IndexError):
            skipped.append("unparsed amounts")
            continue

        description = cells[kind_index + 3] if len(cells) > kind_index + 3 else ""
        ticker = _map_symbol(description)
        if not ticker:
            unmapped.append(description[:44])
            continue

        # Price is recorded exactly as the contract note prints it, and the
        # settled amount is carried in the note rather than being turned into
        # a derived fee.
        #
        # The two do not reconcile consistently. The Price column tracks the
        # instrument's USD price closely, and on two rows the EUR debit is
        # exactly price x quantity divided by that day's EUR/USD - but on four
        # others the implied rate is nowhere near the market. Deriving a
        # "fee" from that gap would report an 8.9% commission on one PLTR
        # purchase, which is an artefact of the mismatch and not a cost that
        # was charged. Faithful to the document beats confidently wrong, so
        # these rows are flagged for review instead.
        settled = abs(_money(cells[kind_index + 4])) if len(cells) > kind_index + 4 else 0.0
        currency = "USD" if "USD" in description.upper() else "EUR"
        implied = (price * shares / settled) if settled else 0.0
        note = f"Davy {cells[0][:12]} · settled EUR {settled:.2f}"
        if settled and not (0.98 <= implied <= 1.02) and currency == "EUR":
            note += " · CHECK currency"

        trades.append(Trade(
            ticker=ticker,
            action=BUY if cells[kind_index].upper() == "BOUGHT" else SELL,
            shares=shares,
            price=price,
            currency=currency,
            date=when,
            portfolio=portfolio,
            note=note.strip(),
        ))

    return ImportResult(trades, skipped, unmapped, "Davy XLS")


def detect(path: str | Path, portfolio: str) -> ImportResult:
    """Pick a parser from the file itself rather than its extension."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return trading212_csv(path, portfolio)
    if suffix in (".xls", ".xlsx"):
        return davy_xls(path, portfolio)
    if suffix == ".pdf":
        raise ValueError(
            "PDF statements are not importable. The Trading 212 PDF embeds a "
            "font with no ToUnicode mapping for digits, so every number "
            "extracts as a null byte - the customer name alone comes out as "
            "'C t lin Bond ri'. Export the CSV instead."
        )
    raise ValueError(f"no importer for {suffix or path.name}")


#: Pension fund name -> a Yahoo line that behaves like it, where one exists.
#: Occupational scheme funds are unlisted, so most have no ticker and are
#: carried at stated value. The proxy is only used to give the pension a
#: return series to simulate from; it is labelled as a proxy on screen.
PENSION_PROXIES = {
    "ILIM INDEXED NORTH AMERICAN EQUITY": "CSPX.AS",   # S&P 500 tracker
    "ILIM PASSIVE GLOBAL EQUITY": "IWDA.AS",
    "DAVY GLOBAL EQUITIES FOUNDATION": "IWDA.AS",
    "DAVY GLOBAL FUNDAMENTALS EQUITY": "IWDA.AS",
    "DAVY LONG TERM GROWTH": "IWDA.AS",
    # A multi-asset fund, so a pure equity tracker is the wrong shape. The
    # 60/40-ish mandate is closer to a global equity/bond blend, and the
    # aggregate bond line is the honest proxy for the half that is not
    # equity; using IWDA alone would overstate its volatility.
    "DAVY MODERATE GROWTH": "AGGH.AS",
}


def _pension_proxy(name: str) -> str:
    key = name.upper()
    for fragment, ticker in PENSION_PROXIES.items():
        if fragment in key:
            return ticker
    return ""


def wtw_pension_pdf(path: str | Path) -> dict:
    """Parse a WTW occupational pension statement.

    Unlike the broker PDF, this one embeds a font with a usable ToUnicode
    map, so the numbers survive extraction. Two things come out of it: the
    fund holdings with unit counts and prices, and the contribution history
    split by employee and employer.
    """
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    holdings, contributions = [], []
    money = lambda s: float(str(s).replace("€", "").replace(",", "").strip())

    # The holdings table ends where "Investment Split" begins. Without that
    # boundary the contribution rows below match the same shape - date,
    # amount, units, unit price - and get counted a second time as holdings,
    # which doubled the pot.
    try:
        boundary = next(i for i, line in enumerate(lines)
                        if line.startswith("Investment Split"))
    except StopIteration:
        boundary = len(lines)
    holding_lines = lines[:boundary]

    # Holdings: a fund name spread over one or more lines, then value, units,
    # unit price and a price date. Scanned by stepping one line at a time and
    # skipping what a match consumed - advancing by four unconditionally
    # walked into the middle of the next block and silently dropped a fund.
    # -3, not -4: the final fund's date is the last line before the
    # boundary, and the tighter bound excluded the last block entirely.
    i, consumed = 0, -1
    while i < len(holding_lines) - 3:
        if i <= consumed:
            i += 1
            continue
        if (holding_lines[i].startswith("€")
                and re.fullmatch(r"[\d,]+", holding_lines[i + 1] or "")
                and holding_lines[i + 2].startswith("€")
                and re.fullmatch(r"\d{2}/\d{2}/\d{4}", holding_lines[i + 3] or "")):
            name_parts, j = [], i - 1
            while j >= 0 and not holding_lines[j].startswith("€") and \
                    not re.fullmatch(r"\d{2}/\d{2}/\d{4}", holding_lines[j]) \
                    and len(name_parts) < 4:
                name_parts.insert(0, holding_lines[j])
                j -= 1
            # Column headers bleed into the first fund's name.
            name = " ".join(w for w in name_parts
                            if w not in ("Date", "Unit Price", "Number", "of Units",
                                         "Current", "Value", "Fund Name")).strip()
            if name and name not in ("Employee", "Employer") and len(name) > 3:
                holdings.append({
                    "name": name,
                    "value_eur": money(holding_lines[i]),
                    "units": float(holding_lines[i + 1].replace(",", "")),
                    "unit_price": money(holding_lines[i + 2]),
                    "priced_on": holding_lines[i + 3],
                    "ticker": _pension_proxy(name),
                })
            consumed = i + 3
            i += 4
            continue
        i += 1

    # Contributions: date, who, amount, units, unit price, date, amount, fund.
    i = 0
    while i < len(lines) - 7:
        if (re.fullmatch(r"\d{2}/\d{2}/\d{4}", lines[i])
                and lines[i + 1] in ("Employee", "Employer")):
            day, month, year = lines[i].split("/")
            contributions.append({
                "date": f"{year}-{month}-{day}",
                "source": lines[i + 1].lower(),
                "amount_eur": money(lines[i + 2]),
                "note": f"{lines[i + 7]} @ {lines[i + 4]}" if len(lines) > i + 7 else "",
            })
            i += 8
            continue
        i += 1

    # Dedupe: the same fund block is printed once per page section.
    seen, unique = set(), []
    for holding in holdings:
        key = (holding["name"], holding["value_eur"], holding["units"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(holding)

    contributions.sort(key=lambda c: c["date"])

    # Whose statement this is. The scheme covers both of them, and importing
    # one person's pot over the other's would be silent and expensive.
    owner = ""
    for index, line in enumerate(lines):
        if "Pension Scheme" in line and index:
            owner = lines[index - 1].strip()
            break

    return {"holdings": unique, "contributions": contributions,
            "owner": owner, "scheme": next(
                (l for l in lines if "Pension Scheme" in l), ""),
            "source": Path(path).name}
