from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config import cfg
from db import stats_snapshot, get_kv, set_kv, _  # noqa
import logging

router = Router()
log = logging.getLogger("imageconv")

def _is_admin(m: Message) -> bool:
    return m.from_user.id in cfg.admin_ids

@router.message(Command("admin"))
async def admin(m: Message):
    if not _is_admin(m): return
    maint = await get_kv("maintenance", "off")
    await m.answer(
        f"🛠 <b>Admin</b>\n"
        f"Maintenance: <b>{maint}</b>\n\n"
        f"/stats — usage snapshot\n"
        f"/logs — recent errors (check host logs)\n"
        f"/broadcast &lt;text&gt; — announce\n"
        f"/maintenance on|off\n"
    )

@router.message(Command("stats"))
async def stats(m: Message):
    if not _is_admin(m): return
    s = await stats_snapshot()
    await m.answer(
        f"👥 Users: {s['users']}\n"
        f"✅ Conversions: {s['conversions']}\n"
        f"❌ Failures: {s['failures']}\n"
        f"📅 Today: {s['today']}\n"
        f"🏆 Top target: {s['top_dst']}"
    )

@router.message(Command("maintenance"))
async def maintenance(m: Message):
    if not _is_admin(m): return
    parts = (m.text or "").split()
    if len(parts) != 2 or parts[1] not in ("on", "off"):
        await m.answer("Usage: /maintenance on|off"); return
    await set_kv("maintenance", parts[1])
    await m.answer(f"Maintenance is now <b>{parts[1]}</b>.")

@router.message(Command("broadcast"))
async def broadcast(m: Message):
    if not _is_admin(m): return
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        await m.answer("Usage: /broadcast your message"); return
    import aiosqlite
    from db import DB_PATH
    sent = failed = 0
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM users") as cur:
            async for (uid,) in cur:
                try:
                    await m.bot.send_message(uid, text)
                    sent += 1
                except Exception:
                    failed += 1
    await m.answer(f"Broadcast done. Sent: {sent}, failed: {failed}.")
