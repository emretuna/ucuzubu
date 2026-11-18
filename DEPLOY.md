# ucuzu — Railway deployment

Long-polling Telegram bot: no public ports, no domain needed. One always-on
replica with a persistent volume for SQLite.

## Prerequisites

- Railway account (railway.com) — Hobby plan (~$5/mo) is enough; trial credit works too.
- A Telegram bot token from @BotFather (`TELEGRAM_BOT_TOKEN`).
- Railway CLI (`brew install railway`) — **the deploy path since the IaC migration** (see below).

## First-time setup (dashboard)

1. **New Project → Deploy from GitHub repo** → select `ucuzubu` (initial deploy only).
2. **Service → Variables** → add:
   - `TELEGRAM_BOT_TOKEN` = your token
   - `UCUZU_DB_PATH` = `/data/ucuzu.db`
   - optional affiliate vars (see `.env.example`): `AFFILIATE_HEPSIBURADA`,
     `AFFILIATE_TRENDYOL`, `AFFILIATE_N11`, `AFFILIATE_AMAZON_TAG`
   - `FETCH_PROXY_URL` when a residential proxy is available (see contingency).
3. **Service → Volumes** → add a volume mounted at `/data` (1 GB is plenty).
   Without it the cache is lost on every redeploy.
4. Logs should show `ucuzu bot polling started`. Send the bot a product link to verify.

## Shipping code changes (VERIFIED workflow)

**`git push` does NOT auto-deploy anymore.** After the migration to Infrastructure
as Code (`.railway/railway.ts`, which replaced the deprecated `railway.toml`), the
GitHub push trigger was detached — measured: a pushed commit produced no new
deployment after 100 s. The deploy path is:

```bash
railway up -d -y        # uploads the working tree, builds the Dockerfile, deploys
railway deployment list # confirm a new SUCCESS deployment
```

- `railway config apply` manages service settings (builder, start command,
  replicas) from `.railway/railway.ts`; it does not diff the pinned `commitSha`,
  so do not rely on it to ship code.
- Keep pushing to GitHub for history; deploy with `railway up`.
- SSH host key: if `railway ssh` fails with "Host key verification failed", run
  `ssh-keyscan -t ed25519,rsa ssh.railway.com >> ~/.ssh/known_hosts` once.

## CLI prerequisites (one-time)

```bash
brew install railway        # or: npm i -g @railway/cli
railway login               # browser auth
railway link                # pick project marvelous-eagerness / service ucuzubu
railway ssh keys add -k ~/.ssh/id_ed25519.pub   # for railway ssh
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