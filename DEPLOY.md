# ucuzu — Railway deployment

Long-polling Telegram bot: no public ports, no domain needed. One always-on
replica with a persistent volume for SQLite.

## Prerequisites

- Railway account (railway.com) — Hobby plan (~$5/mo) is enough; trial credit works too.
- A Telegram bot token from @BotFather (`TELEGRAM_BOT_TOKEN`).
- This repo pushed to GitHub (Railway deploys from a Git repo), or use the CLI below.

## Deploy (dashboard)

1. **New Project → Deploy from GitHub repo** → select `ucuzu`.
   Railway reads `railway.toml`: Dockerfile build, start command, restart ALWAYS, 1 replica.
2. **Service → Variables** → add:
   - `TELEGRAM_BOT_TOKEN` = your token
   - `UCUZU_DB_PATH` = `/data/ucuzu.db`
   - optional affiliate vars (see `.env.example`): `AFFILIATE_HEPSIBURADA`,
     `AFFILIATE_TRENDYOL`, `AFFILIATE_N11`, `AFFILIATE_AMAZON_TAG`
3. **Service → Volumes** → add a volume mounted at `/data` (1 GB is plenty).
   Without it the cache is lost on every redeploy.
4. Deploy. Logs should show `ucuzu bot polling started`.
5. Send the bot a product link from Telegram to verify.

## Deploy (CLI alternative)

```bash
brew install railway        # or: npm i -g @railway/cli
railway login               # opens browser auth
railway link                # pick the existing project + service
railway up                  # build & deploy from local dir (dashboard Git deploys also work)
railway variables set TELEGRAM_BOT_TOKEN=<token> UCUZU_DB_PATH=/data/ucuzu.db
# add the volume in the dashboard (Service → Volumes → /data), then redeploy
```

## Operational notes

- **Never scale above 1 replica** — two pollers answer the same message twice.
- Redeploys restart the process; in-memory cooldowns reset (harmless), SQLite
  cache survives on the volume.
- Logs: dashboard → service → Deployments → View Logs. Price/parse errors are
  logged per site; a blocked site shows as `— (şu an ulaşılamıyor)` in replies.
- Local check before deploying: `docker build -t ucuzu . && docker run --rm -e TELEGRAM_BOT_TOKEN=<token> ucuzu`.
- **Post-deploy anti-bot check (do this first):** the load-bearing unknown is
  whether Railway's egress IP passes the four sites. Diagnostics ship in the
  image — run them inside the deployed machine (requires `brew install railway`,
  `railway login`, `railway link` first):

  ```bash
  railway ssh
  uv run --no-sync python scripts/diagnose_sites.py   # raw per-site cause: 403 vs page variant
  uv run --no-sync python scripts/smoke_test.py --no-cache   # full acceptance gate
  ```

  All identity checks PASS → egress is fine. Persistent `BlockedError` /
  `site_status: error` → contingency below, not a code bug.

## Anti-bot contingency (important)

Railway egress IPs are **datacenter** IPs. Observed in production: Hepsiburada
(TLS-fingerprint-based Akamai) passes, but Trendyol/n11/Amazon block the
datacenter range via IP reputation. The fetch layer rotates TLS impersonation
profiles (chrome → chrome124 → safari) — that does not fix IP reputation.

**Countermeasure (built): residential proxy via `FETCH_PROXY_URL`.**

1. Confirm with the in-machine smoke gate above; logs show
   `BlockedError … (all impersonates)` for the blocked sites.
2. Get a **Turkish residential** proxy (datacenter exits stay blocked).
   Providers with TR residential pools: Bright Data, Oxylabs, Smartproxy,
   IPRoyal, Proxy-Cheap — pick pay-as-you-go; the bot uses ~30 requests per
   comparison, so traffic is minimal (a few GB/month at most).
3. Railway → Variables → add
   `FETCH_PROXY_URL=http://user:pass@tr.proxy-host:port` → redeploy.
   All fetches (searches, PDPs, shortener resolution) route through it.
4. Re-run the in-machine smoke gate; all four sites should report `ok`.
5. Without a proxy the bot still works partially: unblocked sites compare,
   blocked ones show `— (şu an ulaşılamıyor)`. No headless-browser rewrite
   without sign-off.