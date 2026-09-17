# 🤖 FILE MARKET — Telegram file marketplace

Professional starter bot:
- 📤 users upload files and set a price
- 🛍 buyers browse products
- 💳 Click payment link
- 🔐 Click SHOP API Prepare/Complete webhook
- 📥 file is delivered only after successful Complete callback
- 🧾 purchase history
- 📁 seller's products
- 💾 SQLite database

## 1. Install

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure

Copy `.env.example` to `.env` and fill:
- `BOT_TOKEN`
- `ADMIN_ID`
- `CLICK_SERVICE_ID`
- `CLICK_MERCHANT_ID`
- `CLICK_SECRET_KEY`

Never publish `.env` or the Click secret key to GitHub.

## 3. HTTPS

Click callback must be reachable from the public internet. Put the bot behind an HTTPS reverse proxy such as Nginx/Caddy.

Set Click's Prepare and Complete callback URL to:

`https://YOUR_DOMAIN/click`

The application listens internally on `WEBHOOK_PORT` (default 8080).

## 4. Run

```bash
python bot.py
```

## 5. Important

The code uses Telegram `file_id`, so the original file does not need to be stored on your server.

For production, add:
- PostgreSQL
- Nginx/Caddy + HTTPS
- admin moderation
- seller balance/payout system
- rate limiting
- audit logs
- backups
- terms/refund policy
