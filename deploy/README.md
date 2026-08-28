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

## Every rebuild, from this Mac

```bash
python3 -m fundengine build && ./deploy/deploy.sh
```

Then open `http://46.202.140.61/` and sign in with the htpasswd user.

Note the two halves have different lifetimes. Charts come from `data.json`,
which is a build artefact — a trade recorded through the site updates the
ledger and the sheet immediately, but the charts only move on the next
rebuild. The Transactions and Pension tabs read live from the API, so what
you entered is visible there straight away.

## Rebuilding on a schedule

The build needs the Google service account, which lives on this Mac in the
portfolio-agent checkout — so the rebuild runs here, not on the VPS. If you
want it nightly, add a launchd job rather than cron; cron on macOS does not
get the network entitlements a GUI login session has.

Note the VPS crontab has been clobbered before by another project
installing its schedule with `crontab <file>`, which replaces rather than
merges. Nothing here adds a VPS cron entry, deliberately.
