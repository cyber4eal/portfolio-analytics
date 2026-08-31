# Deploying the Bond Portfolio Centre

One-time setup on the VPS, then `deploy.sh` for every rebuild.

## Why there is a password on it

The page lists your holdings, their euro values and your total. On a public
IP that is personal financial data protected by nothing but an unguessed
URL. Basic auth over plain HTTP is weak — the password crosses the wire in
base64 — so treat it as a lock on an unlocked door until TLS is in front of
it. If this box ever gets a domain, put certbot on it and the same config
works over 443.

## One-time, on the VPS

```bash
apt-get update && apt-get install -y nginx apache2-utils
htpasswd -c /etc/nginx/.htpasswd-fundlab catalin     # prompts for a password
mkdir -p /var/www/fundlab
```

Copy the site config over and enable it:

```bash
scp deploy/nginx-fundlab.conf root@46.202.140.61:/etc/nginx/sites-available/fundlab
```

Then on the VPS:

```bash
ln -sf /etc/nginx/sites-available/fundlab /etc/nginx/sites-enabled/fundlab
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

## The transaction API

The site is static, but recording trades and editing the pension needs a
writer. That runs on the VPS as a small stdlib-only service on localhost,
reached only through nginx, so the basic auth above is the single door to
something that can change share counts in a live spreadsheet.

It imports the portfolio-agent checkout's `sheets_client` rather than
keeping a second copy of the service account or a second idea of which book
a row belongs to. One writer, one set of rules.

```bash
rsync -avz --exclude data --exclude .git ./ root@46.202.140.61:/opt/portfolio-analytics/
```

Then on the VPS:

```bash
cp /opt/portfolio-analytics/deploy/bond-api.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now bond-api && systemctl status bond-api --no-pager
```

Check it answers:

```bash
curl -s localhost:8001/api/health
```

`sheetWrites: true` means AGENT_DIR was found and trades will be mirrored to
the sheet. `false` means the ledger still records everything and the mirror
is off — which is the safe failure, not a broken one.

### What the VPS needs before the refresh button works

The API can start without any of this — recording trades only needs the
standard library — but a rebuild is the full analytics stack, and without it
the button posts, waits, and returns the tail of a traceback.

```bash
pip3 install "numpy>=1.24" "pandas>=2.0" "yfinance>=0.2.40" "requests>=2.32" "google-api-python-client>=2.130" "google-auth>=2.30" "python-dotenv>=1.0"
```

`/api/health` reports what this box can actually do:

| Field | Means |
| --- | --- |
| `rebuildMode: "live sheet"` | reads the Holdings sheet — the real thing |
| `rebuildMode: "last synced holdings"` | no Google credentials, so it reprices `data/holdings.csv` |
| `canRebuild: false` | `rebuildBlockers` says what is missing, in a sentence |

Two environment variables in `bond-api.service` make the difference, and
both are already in the unit file:

* `AGENT_DIR` — the portfolio-agent checkout, for the sheet and its service
  account. The CLI reads it too, so `refresh` works on either machine
  without anyone passing `--agent-dir`.
* `BOND_SITE_DIR=/var/www/fundlab` — where a rebuild writes. The served
  directory is not inside the checkout, so without this a rebuild on the VPS
  wrote a file nginx never reads and reported success while the page stayed
  days old.

## Every rebuild, from this Mac (see the ordering note in deploy.sh)

```bash
python3 -m fundengine build && ./deploy/deploy.sh
```

Then open `http://46.202.140.61/` and sign in with the htpasswd user.

Note the two halves have different lifetimes. Charts come from `data.json`,
which is a build artefact — a trade recorded through the site updates the
ledger and the sheet immediately, but the charts only move on the next
rebuild. The Transactions and Pension tabs read live from the API, so what
you entered is visible there straight away.

## Keeping it current

Three different things go stale at three different rates, and only one of
them is automatic today.

| What | Where it lives | How it refreshes |
| --- | --- | --- |
| Holdings edits, blends, simulations, surfaces | in the page | instantly, from the shipped return matrix |
| Explore lookups | `/api/quote` | live, cached an hour |
| Prices, the four theories, the plan, trend signals, hedges | `site/data.json` | only on a build |
| Ledger and pension | `data/` via the API | immediately on write |

So the age of the build is the age of the advice. The masthead says how old
it is and turns amber past 36 hours, because a rebalance plan computed
against week-old prices looks exactly like one computed against this
morning's.

Three ways it refreshes:

1. **Daily, unattended** — the launchd agent below.
2. **On a trade** — recording a trade through the site triggers a rebuild in
   the background, debounced so three trades in a row cause one rebuild.
3. **On demand** — the *refresh* button in the masthead, which appears
   whenever the API is reachable.

### The daily job

```bash
cp deploy/com.bond.portfolio-refresh.plist ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.bond.portfolio-refresh.plist && launchctl start com.bond.portfolio-refresh
```

It runs at 18:30 and writes to `logs/refresh.log`. Add `--deploy` to the
plist's arguments once the VPS is set up and it will rsync after building.

launchd rather than cron, deliberately: a cron job on macOS runs outside the
GUI login session and does not inherit its network entitlements, so it fails
to reach Yahoo and Google in ways that take an afternoon to diagnose.

It runs on the Mac rather than the VPS because the Google service account
lives here and the build reads the live sheet. The VPS only ever receives
the finished static site.

## Rebuilding on a schedule

The build needs the Google service account, which lives on this Mac in the
portfolio-agent checkout — so the rebuild runs here, not on the VPS. If you
want it nightly, add a launchd job rather than cron; cron on macOS does not
get the network entitlements a GUI login session has.

Note the VPS crontab has been clobbered before by another project
installing its schedule with `crontab <file>`, which replaces rather than
merges. Nothing here adds a VPS cron entry, deliberately.
