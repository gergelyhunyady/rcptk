# Cloudflare Relay for rcptk Telegram Bot

This directory contains the Cloudflare Worker script that relays Telegram messages to GitHub Actions.

## Quick Setup via Cloudflare Dashboard (2 minutes)

1. Go to [dash.cloudflare.com](https://dash.cloudflare.com/) and navigate to **Workers & Pages** -> **Create application** -> **Create Worker**.
2. Name it (e.g. `rcptk-relay`) and click **Deploy**.
3. Click **Edit code** and replace the default content with the code from [`worker.js`](worker.js), then click **Deploy**.
4. Go to the Worker's **Settings** -> **Variables and Secrets** and add:
   - `ALLOWED_USER_ID`: Your numeric Telegram user ID (e.g. `123456789`)
   - `GITHUB_PAT`: GitHub Fine-Grained Personal Access Token (with `Contents: Read and write` on `rcptk`)
   - `TELEGRAM_SECRET`: A random string of your choice (e.g. `mysecrettoken123`)
   - `TELEGRAM_BOT_TOKEN`: Your Telegram Bot token (from @BotFather)
   - `GITHUB_REPO`: `gergelyhunyady/rcptk` (optional, defaults to this repo)

5. Register the webhook with Telegram:
   Open this URL in your browser (replace `<BOT_TOKEN>`, `<WORKER_URL>`, and `<TELEGRAM_SECRET>`):
   ```text
   https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<TELEGRAM_SECRET>
   ```
   Telegram will reply with `{"ok":true,"result":true,"description":"Webhook was set"}`.
