# Image Converter Bot

## Setup
1. `cp .env.example .env` and fill BOT_TOKEN + ADMIN_IDS.
2. `pip install -r requirements.txt`
3. `python bot.py`

## Docker
`docker compose up -d --build`

## Notes / known limits
- HEIC/AVIF read requires libheif (installed in Dockerfile). AVIF *write* depends on
  Pillow build; verify with `python -c "from PIL import features; print(features.check('avif'))"`.
- FSM state is in-memory. For multi-instance deployment, swap MemoryStorage for
  aiogram's RedisStorage and run one worker per instance.
- Broadcast iterates users sequentially; for >10k users, chunk with asyncio.sleep.
- Daily limit stored in SQLite; safe for single-instance. Use Redis for multi-instance.
- Background removal (rembg) is intentionally NOT included — it's a ~100MB ONNX model
  and would violate the "avoid unnecessary resource consumption" rule unless you want it.
- File size limit in config defaults to 20MB. Telegram bot API caps *downloads* at 20MB
  unless you're on a local Bot API server — raise accordingly if you run one.
