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

## Every rebuild, from this Mac

```bash
python3 -m fundengine build && ./deploy/deploy.sh
```

Then open `http://46.202.140.61/` and sign in with the htpasswd user.

## Rebuilding on a schedule

The build needs the Google service account, which lives on this Mac in the
portfolio-agent checkout — so the rebuild runs here, not on the VPS. If you
want it nightly, add a launchd job rather than cron; cron on macOS does not
get the network entitlements a GUI login session has.

Note the VPS crontab has been clobbered before by another project
installing its schedule with `crontab <file>`, which replaces rather than
merges. Nothing here adds a VPS cron entry, deliberately.
