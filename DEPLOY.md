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
npm i -g @railway/cli
railway login
railway init          # link/create project
railway up            # build & deploy from local dir
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
  whether Railway's egress IP passes the four sites. The smoke gate ships in
  the image — run it inside the deployed machine:

  ```bash
  railway ssh
  uv run --no-sync python scripts/smoke_test.py --no-cache
  ```

  All identity checks PASS → egress is fine. Persistent `BlockedError` /
  `site_status: error` → contingency below, not a code bug.

## Anti-bot contingency (important)

Railway egress IPs are **datacenter** IPs. The scrapers were verified from a
Turkish residential IP; Akamai/Amazon may 403 datacenter ranges. The fetch layer
already rotates TLS impersonation profiles (chrome → chrome124 → safari). If a
site stays blocked in production:

1. Run the in-machine smoke gate above; check logs for `BlockedError … (all impersonates)`.
2. A platform-side 403 is the contingency trigger, not a bug: first countermeasure
   is a residential proxy (`FETCH_PROXY_URL` support is the plan's documented next
   step — curl_cffi `proxies=`; ask for it when needed). No headless-browser
   rewrite without sign-off.
3. A blocked site degrades gracefully: the other three still compare.