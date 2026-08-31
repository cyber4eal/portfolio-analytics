"""python3 -m fundengine build [--agent-dir PATH] [--allocation 0.10]"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import importers, ledger, pension, publish

#: The VPS keeps the agent checkout somewhere else entirely, and the service
#: unit already says where in AGENT_DIR. Reading it means `refresh` works on
#: both machines without anyone remembering to pass --agent-dir - which is
#: exactly what the rebuild button was failing on.
_AGENT_DIR_ENV = os.environ.get("AGENT_DIR", "").strip()

FALLBACK_AGENT_DIR = (
    "/Users/catalin_main/Library/Application Support/Claude/"
    "local-agent-mode-sessions/b6bac09d-05f2-42ba-bfcf-deef8d796d7d/"
    "aea9c31f-2d00-462a-a14b-1a723c07a247/"
    "local_143c8db9-8af1-4880-98e5-77b3e0d4f58a/outputs/portfolio-agent"
)
DEFAULT_AGENT_DIR = _AGENT_DIR_ENV or FALLBACK_AGENT_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fundengine")
    parser.add_argument("command",
                        choices=["build", "refresh", "import", "import-pension"])
    parser.add_argument("--deploy", action="store_true",
                        help="rsync the built site to the VPS after building")
    parser.add_argument("--quiet", action="store_true",
                        help="only print on failure - for scheduled runs")
    parser.add_argument("--file", action="append", default=[],
                        help="broker statement to import (repeatable)")
    parser.add_argument("--portfolio", default="Catalin",
                        help="which book the imported trades belong to")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it the import is a dry run")
    parser.add_argument("--agent-dir", default=DEFAULT_AGENT_DIR,
                        help="portfolio-agent checkout holding .env and secrets/")
    parser.add_argument("--allocation", type=float, default=0.10,
                        help="trial weight when ranking funds as additions")
    parser.add_argument("--from-csv", default=None,
                        help="rebuild from a data/holdings.csv snapshot instead "
                             "of the live sheet (no Google credentials needed)")
    args = parser.parse_args(argv)

    if args.command == "refresh":
        return _refresh(args)
    if args.command == "import":
        return _import(args)
    if args.command == "import-pension":
        return _import_pension(args)

    payload = publish.build(args.agent_dir, allocation=args.allocation,
                            from_csv=args.from_csv)
    publish.write(payload)
    publish.write_snapshot(payload)
    print(f"Done, as at {payload['asOf']}.")
    return 0



def _import(args) -> int:
    """Read broker statements into the ledger.

    A dry run by default. These files change what every cost basis and
    realised figure says, so writing them is an explicit choice rather than
    a side effect of pointing at a file.
    """
    if not args.file:
        print("nothing to import - pass --file")
        return 1

    existing = {(t["date"], t["ticker"], t["action"], round(float(t["shares"]), 6))
                for t in ledger.read_all()}
    fresh, duplicates = [], 0

    for path in args.file:
        try:
            result = importers.detect(path, args.portfolio)
        except ValueError as exc:
            print(f"{path}: {exc}")
            continue
        print(result.summary())
        for trade in result.trades:
            key = (trade.date, trade.ticker, trade.action, round(trade.shares, 6))
            if key in existing:
                duplicates += 1
                continue
            existing.add(key)
            fresh.append(trade)

    print(f"\n{len(fresh)} new trade(s), {duplicates} already in the ledger")
    if not fresh:
        return 0

    by_ticker = {}
    for trade in fresh:
        by_ticker.setdefault(trade.ticker, []).append(trade)
    for ticker in sorted(by_ticker):
        rows = by_ticker[ticker]
        buys = sum(t.shares for t in rows if t.action == ledger.BUY)
        sells = sum(t.shares for t in rows if t.action == ledger.SELL)
        print(f"  {ticker:8} {len(rows):>3} trades, +{buys:g} -{sells:g}")

    if not args.apply:
        print("\ndry run - nothing written. Re-run with --apply to commit.")
        return 0

    for trade in fresh:
        ledger.append(trade)
    print(f"\nwrote {len(fresh)} trade(s) to {ledger.LEDGER}")
    return 0


def _import_pension(args) -> int:
    if not args.file:
        print("nothing to import - pass --file")
        return 1

    # Statements are routed by the name printed on them. The scheme covers
    # both of them and importing one pot over the other would be silent.
    by_owner: dict = {}
    for path in args.file:
        parsed = importers.wtw_pension_pdf(path)
        owner = (parsed.get("owner") or "").split()[0] or args.portfolio
        slot = by_owner.setdefault(owner, {"holdings": [], "contributions": []})
        for holding in parsed["holdings"]:
            if not any(h["name"] == holding["name"] for h in slot["holdings"]):
                slot["holdings"].append(holding)
        for row in parsed["contributions"]:
            key = (row["date"], row["source"], row["amount_eur"])
            if not any((c["date"], c["source"], c["amount_eur"]) == key
                       for c in slot["contributions"]):
                slot["contributions"].append(row)
        print(f"{parsed['source']}: {parsed.get('owner') or 'unknown owner'} — "
              f"{len(parsed['holdings'])} fund(s), "
              f"{len(parsed['contributions'])} contribution(s)")

    for owner, slot in by_owner.items():
        holdings, contributions = slot["holdings"], slot["contributions"]

        pot = sum(h["value_eur"] for h in holdings)
        paid = sum(c["amount_eur"] for c in contributions)
        print(f"\n{owner}: pot EUR {pot:,.0f}, contributions EUR {paid:,.0f}")
        for h in holdings:
            print(f"    {h['name'][:44]:44} EUR {h['value_eur']:>8,.0f}"
                  f"  proxy {h['ticker'] or '-'}")

        if not args.apply:
            continue

        pension.set_holdings([{
            "name": h["name"], "value_eur": h["value_eur"], "units": h["units"],
            "ticker": h["ticker"], "provider": "WTW / J&E Davy scheme",
            "note": (f"unit price EUR {h['unit_price']:g} at {h['priced_on']}"
                     + (f"; returns proxied by {h['ticker']}" if h["ticker"] else "")),
        } for h in holdings], owner=owner)

        known = {(c["date"], c["source"], c["amount_eur"])
                 for c in pension.load(owner).contributions}
        added = 0
        for c in contributions:
            if (c["date"], c["source"], c["amount_eur"]) in known:
                continue
            pension.add_contribution(c, owner=owner)
            added += 1
        print(f"    wrote {len(holdings)} holding(s) and {added} contribution(s)")

    if not args.apply:
        print("\ndry run - nothing written. Re-run with --apply to commit.")
    return 0


def _refresh(args) -> int:
    """Rebuild, and optionally push to the VPS. Meant for a scheduler.

    Quiet on success and loud on failure, because a daily job that chatters
    gets ignored and then its failures get ignored with it. Errors go to
    stderr with a stack trace so a launchd log is worth reading.
    """
    import io
    import contextlib
    import subprocess
    import traceback

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer if args.quiet else sys.stdout):
            payload = publish.build(args.agent_dir, allocation=args.allocation,
                                    from_csv=args.from_csv)
            publish.write(payload)
            publish.write_snapshot(payload)
    except Exception:                                         # noqa: BLE001
        sys.stderr.write(buffer.getvalue())
        traceback.print_exc()
        return 1

    if not args.quiet:
        print(f"Refreshed, prices as at {payload['asOf']}.")

    if args.deploy:
        script = Path(__file__).resolve().parent.parent / "deploy" / "deploy.sh"
        result = subprocess.run([str(script)], capture_output=True, text=True)
        if result.returncode != 0:
            sys.stderr.write(buffer.getvalue())
            sys.stderr.write(result.stdout + result.stderr)
            return result.returncode
        if not args.quiet:
            print(result.stdout.strip().splitlines()[-1] if result.stdout else "Deployed.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
