import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import cfg
from utils import setup_logging, log
from db import init_db, get_kv
from handlers import start, convert, tools, batch, admin

async def main():
    setup_logging()
    await init_db()

    bot = Bot(cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(convert.router)
    dp.include_router(tools.router)
    dp.include_router(batch.router)

    # Maintenance gate
    @dp.message.middleware()
    async def maintenance_mw(handler, event, data):
        if isinstance(event, type(None)):
            return await handler(event, data)
        if getattr(event, "from_user", None) and event.from_user.id in cfg.admin_ids:
            return await handler(event, data)
        if await get_kv("maintenance", "off") == "on":
            try:
                await event.answer("🛠 Bot is under maintenance. Please try later.")
            except Exception:
                pass
            return
        return await handler(event, data)

    log.info("Bot starting…")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        log.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Shutdown.")
