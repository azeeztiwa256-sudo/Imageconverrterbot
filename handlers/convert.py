import asyncio
from pathlib import Path
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import cfg
from db import upsert_user, record_conversion, bump_daily
from keyboards import format_choice, cancel
from services import files, imaging
from utils import log

router = Router()
_executor_lock = asyncio.Lock()   # throttles concurrent CPU-heavy jobs
_sem = asyncio.Semaphore(4)       # max parallel Pillow jobs

class Conv(StatesGroup):
    awaiting_format = State()

@router.message(F.photo | F.document)
async def receive_image(m: Message, state: FSMContext):
    await upsert_user(m.from_user.id, m.from_user.username)

    limit = await bump_daily(m.from_user.id)
    if limit > cfg.daily_limit and m.from_user.id not in cfg.admin_ids:
        await m.answer(f"⚠️ Daily limit ({cfg.daily_limit}) reached. Try again tomorrow.")
        return

    tg_file = None
    if m.photo:
        tg_file = await m.bot.get_file(m.photo[-1].file_id)
    elif m.document and (m.document.mime_type or "").startswith("image/"):
        tg_file = await m.bot.get_file(m.document.file_id)
    else:
        await m.answer("Send an image (as photo or file).")
        return

    size_mb = (tg_file.file_size or 0) / 1024 / 1024
    if size_mb > cfg.max_file_mb:
        await m.answer(f"❌ File too large ({size_mb:.1f} MB). Limit: {cfg.max_file_mb} MB.")
        return

    suffix = Path(tg_file.file_path or "x").suffix or ".img"
    local = files.temp_path(suffix)
    await m.bot.download_file(tg_file.file_path, destination=local)

    fmt = await asyncio.to_thread(files.sniff_image, local)
    if not fmt:
        files.cleanup(local)
        await m.answer("❌ That doesn't look like a valid image.")
        return

    await state.set_state(Conv.awaiting_format)
    await state.update_data(src=str(local), src_fmt=fmt, bytes_in=local.stat().st_size)
    await m.answer(
        f"✅ Detected <b>{fmt}</b> ({local.stat().st_size/1024:.1f} KB).\nChoose output format:",
        reply_markup=format_choice(),
    )

@router.callback_query(Conv.awaiting_format, F.data.startswith("conv:"))
async def do_convert(cb: CallbackQuery, state: FSMContext):
    dst = cb.data.split(":", 1)[1]
    data = await state.get_data()
    src = Path(data["src"])
    await state.clear()

    await cb.message.edit_text(f"⏳ Converting to {dst}…")
    try:
        async with _sem:
            out, size_out = await asyncio.to_thread(
                imaging.convert, src, dst, cfg.temp_dir
            )
        await cb.message.answer_document(
            out.open("rb"),
            filename=out.name,
            caption=f"✅ {data['src_fmt']} → {dst}\n{size_out/1024:.1f} KB",
        )
        await record_conversion(cb.from_user.id, data["src_fmt"], dst,
                                data["bytes_in"], size_out, True)
    except Exception as e:
        log.exception("convert failed")
        await cb.message.answer("❌ Conversion failed. The file may be corrupted.")
        await record_conversion(cb.from_user.id, data.get("src_fmt"), dst,
                                0, 0, False)
    finally:
        files.cleanup(src)
        if 'out' in locals():
            files.cleanup(out)

@router.callback_query(F.data == "cancel")
async def do_cancel(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "src" in data:
        files.cleanup(Path(data["src"]))
    await state.clear()
    await cb.message.edit_text("Cancelled.")
