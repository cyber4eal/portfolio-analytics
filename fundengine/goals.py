"""The mortgage deposit goal, read from the sheet that already tracks it.

The cash in these accounts is not idle and it is not part of the investable
book. It is a deposit being assembled to a date, which makes it a different
kind of money: its job is to be intact and available in February, not to
compound. Showing it inside portfolio weights would be wrong twice over -
it would dilute every risk number, and it would invite the suggestion to
invest it, which is the one thing that must not happen to money with a
short, fixed deadline.

The sheet's own Goal_Mortgage tab is the source. It is read rather than
recomputed so that the site and the spreadsheet cannot drift apart on the
number that matters.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path


def _number(text) -> float:
    cleaned = str(text or "").replace("€", "").replace(",", "").replace("%", "").strip()
    if not cleaned or cleaned in ("-", "."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def read_goal(agent_dir: str | os.PathLike, tab: str = "Goal_Mortgage") -> dict:
    """Pull the goal parameters and the earmarked balances."""
    from dotenv import load_dotenv
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    agent_path = Path(agent_dir)
    load_dotenv(agent_path / ".env")

    key_file = Path(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
    if not key_file.is_absolute():
        key_file = agent_path / key_file
    creds = service_account.Credentials.from_service_account_file(
        str(key_file), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    sheet_id = os.environ.get("ANALYTICS_SHEET_ID")
    if not sheet_id:
        return {}
    rows = (sheets.spreadsheets().values()
            .get(spreadsheetId=sheet_id, range=f"'{tab}'!A1:D40")
            .execute().get("values", []))

    fields: dict[str, str] = {}
    for row in rows:
        if len(row) >= 2 and str(row[0]).strip():
            fields[str(row[0]).strip()] = str(row[1]).strip()

    target = _number(fields.get("Target Deposit Amount (€)"))
    if not target:
        return {}

    return {
        "target": target,
        "targetDate": fields.get("Target Date", ""),
        "monthlyContribution": _number(fields.get("Monthly Contribution (€)")),
        "sheetHeld": _number(fields.get("Total Earmarked Today")),
        "includeMoneyMarket": (fields.get("Include MMF in Target?", "YES").upper() == "YES"),
        "sheetStatus": fields.get("Status", ""),
    }


#: Rows that are deposit money rather than investments.
EARMARKED = ("Mortgage Deposit", "Money Market Fund")


def track(goal: dict, holdings: list, as_of: date | None = None) -> dict:
    """Measure the goal against every earmarked balance in both books.

    The sheet's tracker counts one person's balances. If the other book is
    also holding deposit money for the same house, the goal is much further
    along than the sheet says - and the opposite mistake, counting money
    that is earmarked for something else, would be worse. Both totals are
    reported so the difference is visible rather than assumed.
    """
    if not goal:
        return {}
    as_of = as_of or date.today()

    by_book: dict[str, float] = {}
    lines = []
    for holding in holdings:
        if holding.symbol not in EARMARKED:
            continue
        if holding.symbol == "Money Market Fund" and not goal.get("includeMoneyMarket", True):
            continue
        by_book[holding.portfolio] = by_book.get(holding.portfolio, 0.0) + holding.value_eur
        lines.append({"book": holding.portfolio, "name": holding.symbol,
                      "value": round(holding.value_eur, 2)})

    held = sum(by_book.values())
    target = goal["target"]
    gap = max(0.0, target - held)

    months = None
    if goal.get("targetDate"):
        try:
            deadline = date.fromisoformat(goal["targetDate"][:10])
            months = max(0, (deadline.year - as_of.year) * 12 + deadline.month - as_of.month)
        except ValueError:
            months = None

    required = (gap / months) if months else None
    planned = goal.get("monthlyContribution", 0.0)

    return {
        **goal,
        "held": round(held, 2),
        "byBook": {k: round(v, 2) for k, v in sorted(by_book.items())},
        "lines": sorted(lines, key=lambda r: -r["value"]),
        "gap": round(gap, 2),
        "pct": round(100 * held / target, 1) if target else 0.0,
        "monthsRemaining": months,
        "requiredMonthly": round(required, 2) if required is not None else None,
        "shortfallMonthly": (round(required - planned, 2)
                             if required is not None and required > planned else 0.0),
        "onTrack": bool(required is not None and required <= planned),
        # What the sheet's own tracker says, which counts one book only.
        "sheetHeld": goal.get("sheetHeld", 0.0),
        "countedElsewhere": round(held - goal.get("sheetHeld", 0.0), 2),
    }
