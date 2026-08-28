#!/usr/bin/env python3
"""Transaction API for the Bond Portfolio Centre.

Standard library only. The VPS already has the portfolio-agent checkout with
its service account, so this imports that project's sheets_client rather than
holding a second copy of the credentials or a second idea of how the sheet is
laid out. One writer, one set of rules about which book a row belongs to.

It binds to localhost and is meant to sit behind the same nginx basic auth as
the static site. It must not be exposed directly: it can change share counts
in a live spreadsheet, and share counts are the one column that cannot be
recomputed from anything else.

    AGENT_DIR=/root/portfolio-agent python3 server/api.py --port 8001
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fundengine import advice, ledger, pension    # noqa: E402

AGENT_DIR = os.environ.get("AGENT_DIR", "")
MAX_BODY = 64 * 1024

#: A rebuild takes tens of seconds and hits Yahoo, so recording three trades
#: in a row should cause one rebuild, not three.
REBUILD_DEBOUNCE_SECONDS = 90
_rebuild_lock = threading.Lock()
_rebuild_state = {"running": False, "queuedAt": 0.0, "lastFinished": 0.0,
                  "lastResult": None}


def _run_rebuild() -> None:
    """Rebuild the payload in the background.

    Recording a trade changes the sheet immediately but the charts come from
    a build artefact, so without this the page would keep showing the old
    book until someone remembered to rebuild. Failures are recorded and
    surfaced rather than raised: a failed rebuild must not lose the trade
    that triggered it.
    """
    import subprocess

    try:
        result = subprocess.run(
            [sys.executable, "-m", "fundengine", "refresh", "--quiet"],
            cwd=str(REPO), capture_output=True, text=True, timeout=600)
        _rebuild_state["lastResult"] = (
            "ok" if result.returncode == 0
            else f"failed: {(result.stderr or '').strip()[-300:]}")
    except Exception as exc:                                  # noqa: BLE001
        _rebuild_state["lastResult"] = f"failed: {type(exc).__name__}: {exc}"
    finally:
        _rebuild_state["running"] = False
        _rebuild_state["lastFinished"] = time.time()


def request_rebuild(force: bool = False) -> str:
    with _rebuild_lock:
        if _rebuild_state["running"]:
            return "already running"
        since = time.time() - _rebuild_state["lastFinished"]
        if not force and since < REBUILD_DEBOUNCE_SECONDS:
            return f"skipped, rebuilt {since:.0f}s ago"
        _rebuild_state["running"] = True
        _rebuild_state["queuedAt"] = time.time()
    threading.Thread(target=_run_rebuild, daemon=True).start()
    return "started"


def _sheets_client():
    """The agent's sheets_client, imported lazily.

    Lazily because the ledger half of this API has to keep working on a box
    with no Google credentials - recording what you did is more important
    than mirroring it, and losing the mirror should not lose the record.
    """
    if not AGENT_DIR:
        raise RuntimeError("AGENT_DIR is not set, so the sheet cannot be updated")
    scripts = Path(AGENT_DIR) / "scripts"
    if not scripts.exists():
        raise RuntimeError(f"no portfolio-agent scripts at {scripts}")
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    os.chdir(AGENT_DIR)                        # the agent's .env is read relatively
    from dotenv import load_dotenv
    load_dotenv(Path(AGENT_DIR) / ".env")
    import sheets_client
    return sheets_client


_QUOTE_CACHE: dict = {}


def _quote(ticker: str) -> dict:
    """Live lookup for a ticker, held or not.

    Same shape of answer as the Telegram bot's /quote plus enough return
    history for the page to measure the thing against the book. Cached for
    an hour: Yahoo is rate-limited and this sits behind a single-user page,
    so the same ticker gets looked up repeatedly while someone reads it.
    """
    import time as _time

    hit = _QUOTE_CACHE.get(ticker)
    if hit and _time.time() - hit[0] < 3600:
        return hit[1]

    import yfinance as yf

    out = {"ticker": ticker}
    try:
        handle = yf.Ticker(ticker)
        info = handle.info or {}
        dividend = info.get("dividendYield")
        out.update({
            "name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector") or info.get("category") or "",
            "country": info.get("country") or "",
            "currency": info.get("currency") or "",
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "marketCap": info.get("marketCap"),
            "pe": info.get("trailingPE"),
            # Yahoo reports this as a percent, not a fraction.
            "dividendYield": (float(dividend) / 100) if dividend else None,
            "high52": info.get("fiftyTwoWeekHigh"),
            "low52": info.get("fiftyTwoWeekLow"),
            "quoteType": info.get("quoteType"),
        })
    except Exception as exc:                                  # noqa: BLE001
        out["infoError"] = f"{type(exc).__name__}"

    try:
        history = yf.download(ticker, period="5y", interval="1d",
                              auto_adjust=True, progress=False)
        closes = history["Close"]
        if hasattr(closes, "columns"):
            closes = closes.iloc[:, 0]
        closes = closes.dropna()
        if len(closes) > 30:
            returns = closes.pct_change(fill_method=None).dropna()
            out["returns"] = {
                "dates": [d.date().isoformat() for d in returns.index],
                "values": [round(float(v), 6) for v in returns],
            }
            if not out.get("price"):
                out["price"] = round(float(closes.iloc[-1]), 4)
    except Exception as exc:                                  # noqa: BLE001
        out["historyError"] = f"{type(exc).__name__}"

    _QUOTE_CACHE[ticker] = (_time.time(), out)
    return out


class Handler(SimpleHTTPRequestHandler):
    """API on /api/*, static site on everything else.

    Serving both from one process is only for running this on a laptop. On
    the VPS nginx serves the files and proxies /api, which is what the
    basic auth is attached to.
    """

    server_version = "BondPortfolioCentre/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -------- plumbing --------

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    # -------- routes --------

    def do_GET(self):
        route = urlparse(self.path)
        if not route.path.startswith("/api/"):
            return super().do_GET()
        query = parse_qs(route.query)
        portfolio = (query.get("portfolio") or [None])[0]
        try:
            if route.path == "/api/rebuild":
                return self._send(200, {"status": _rebuild_state["lastResult"],
                                        "running": _rebuild_state["running"],
                                        "lastFinished": _rebuild_state["lastFinished"]})
            if route.path == "/api/health":
                return self._send(200, {
                    "ok": True,
                    "rebuild": _rebuild_state["lastResult"],
                    "rebuilding": _rebuild_state["running"],
                    "sheetWrites": bool(AGENT_DIR),
                    "ledger": str(ledger.LEDGER),
                    "trades": len(ledger.read_all()),
                })
            if route.path == "/api/quote":
                ticker = (query.get("ticker") or [""])[0].strip().upper()
                if not ticker:
                    return self._send(400, {"error": "ticker is required"})
                return self._send(200, _quote(ticker))
            if route.path == "/api/pension":
                return self._send(200, pension.accrue(pension.summary()))
            if route.path == "/api/transactions":
                trades = ledger.read_all()
                if portfolio and portfolio != "Combined":
                    trades = [t for t in trades if t.get("portfolio") == portfolio]
                return self._send(200, {
                    "trades": trades,
                    "summary": ledger.summary(ledger.read_all(),
                                              None if portfolio == "Combined" else portfolio),
                })
            return self._send(404, {"error": f"no route {route.path}"})
        except Exception as exc:                                  # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {"error": str(exc)})

    def do_POST(self):
        route = urlparse(self.path)
        try:
            body = self._body()
        except Exception as exc:                                  # noqa: BLE001
            return self._send(400, {"error": f"bad request body: {exc}"})

        try:
            if route.path == "/api/transactions":
                return self._record(body)
            if route.path == "/api/pension/holdings":
                pension.set_holdings(body.get("holdings") or [])
                request_rebuild()
                return self._send(200, pension.accrue(pension.summary()))
            if route.path == "/api/pension/contribution":
                pension.add_contribution(body)
                return self._send(201, pension.accrue(pension.summary()))
            if route.path == "/api/pension/rate":
                # A pay rise makes the trailing average the wrong thing to
                # project forward; this pins what to use instead.
                raw = body.get("monthly")
                pension.set_contribution_override(
                    None if raw in (None, "", "auto") else float(raw))
                return self._send(200, pension.accrue(pension.summary()))
            if route.path == "/api/rebuild":
                return self._send(202, {"rebuild": request_rebuild(force=True)})
            if route.path == "/api/size":
                return self._send(200, advice.size_position(
                    side=body.get("side", "buy"),
                    conviction=float(body.get("conviction", 5)),
                    price=float(body.get("price") or 0),
                    current_weight=float(body.get("currentWeight") or 0),
                    position_value=float(body.get("positionValue") or 0),
                    position_shares=body.get("positionShares"),
                    speculative=bool(body.get("speculative")),
                ))
            return self._send(404, {"error": f"no route {route.path}"})
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:                                  # noqa: BLE001
            traceback.print_exc()
            return self._send(500, {"error": str(exc)})

    def _record(self, body: dict) -> None:
        """Write the ledger first, then mirror to the sheet.

        Order matters. If the sheet write fails - quota, network, a renamed
        tab - the trade is still recorded and the response says the mirror
        did not happen, so it can be replayed. The reverse order would lose
        the record of a trade that had already moved real share counts.
        """
        trade = ledger.Trade(
            ticker=str(body.get("ticker", "")).strip().upper(),
            action=str(body.get("action", "")).strip().lower(),
            shares=float(body.get("shares") or 0),
            price=float(body.get("price") or 0),
            date=str(body.get("date", "")).strip(),
            portfolio=str(body.get("portfolio", "")).strip(),
            currency=str(body.get("currency", "EUR")).strip() or "EUR",
            fee=float(body.get("fee") or 0),
            note=str(body.get("note", "")).strip(),
        )
        ledger.append(trade)

        result = {"trade": trade.as_dict(), "sheet": "skipped"}
        if not body.get("applyToSheet", True):
            result["sheet"] = "not requested"
            return self._send(201, result)

        try:
            client = _sheets_client()
            delta = trade.shares if trade.action == ledger.BUY else -trade.shares
            client.log_trade(
                ticker=trade.ticker, shares_delta=delta, action=trade.action,
                price=trade.price, name=body.get("name") or None,
                currency=trade.currency, portfolio=trade.portfolio,
            )
            result["sheet"] = "updated"
            trade.applied_to_sheet = True
            # The charts come from a build artefact, so a trade that moved
            # the sheet has to move the payload too.
            result["rebuild"] = request_rebuild()
        except Exception as exc:                                  # noqa: BLE001
            traceback.print_exc()
            result["sheet"] = "failed"
            result["sheetError"] = str(exc)

        self._send(201, result)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1",
                        help="localhost by default - this must sit behind nginx")
    parser.add_argument("--static", default=None,
                        help="also serve this directory (local use; nginx does it on the VPS)")
    args = parser.parse_args()

    handler = (partial(Handler, directory=str(Path(args.static).resolve()))
               if args.static else Handler)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    if args.static:
        print(f"Serving {args.static} alongside the API")
    print(f"Transaction API on http://{args.host}:{args.port}"
          f"  sheet writes: {'on' if AGENT_DIR else 'off (AGENT_DIR unset)'}")
    print(f"Ledger: {ledger.LEDGER}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
