# Telegram Image Converter Bot

A Python 3.12 / aiogram 3 Telegram bot for converting images to JPG, PNG, WEBP, BMP, GIF, TIFF and ICO.

## Important fix for your Render deployment

Your deployment log says `ADMIN_IDS is not set`. This starter project does **not** require `ADMIN_IDS` to start. Admin-only commands are disabled for everyone until you configure an administrator ID. `BOT_TOKEN` is required.

## Deploy on Render

1. Push this project to a GitHub repository.
2. In Render, create a **Background Worker** from that repository (not a Web Service).
3. Choose Docker as the runtime / use the included `Dockerfile`.
4. Add environment variable `BOT_TOKEN` with the token from `@BotFather`.
5. Optionally set `ADMIN_IDS` to your numeric Telegram user ID, e.g. `123456789`.
6. Deploy.

The included `render.yaml` describes a worker. Review the instance plan and persistent storage options in Render before production use.

## Run locally

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, put your real `BOT_TOKEN` in `.env`, then run:

```bash
python main.py
```

## Commands

- `/start` — start the bot
- `/help` — usage instructions
- `/stats` — admin-only basic statistics (requires `ADMIN_IDS`)

## Notes and limitations

- This is a functional starter implementation, not a claim that every format/feature in the earlier feature wishlist is implemented.
- SVG is intentionally not accepted as raster input by Pillow in this setup; SVG conversion requires a separately sandboxed renderer.
- Animated GIF handling and format support depend on the source image and installed codec support.
- ICO output is intended for icon-sized images.
- The SQLite database is local. Configure persistent storage on your host if you need data to survive worker replacement/redeployment.
- For production, add automated tests, rate limits, expiry cleanup for abandoned uploads, and monitoring before opening the bot to high traffic.
- Never commit `.env` or your real Telegram token to GitHub.
