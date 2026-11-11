from pathlib import Path
import asyncio
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import cfg
from keyboards import tools_menu, compress_quality, resize_presets, cancel
from services import files, imaging
from db import record_conversion
from utils import log

router = Router()
_sem = asyncio.Semaphore(4)

class Tool(StatesGroup):
    choosing = State()
    waiting_watermark_text = State()
    waiting_custom_size = State()

@router.callback_query(F.data == "menu:tools")
async def show_tools(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Send an image first, then choose a tool.", show_alert=True)
        return
    await state.set_state(Tool.choosing)
    await cb.message.answer("Pick a tool:", reply_markup=tools_menu())

@router.callback_query(Tool.choosing, F.data == "tool:watermark")
async def ask_watermark_text(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Tool.waiting_watermark_text)
    await cb.message.answer("Send the watermark text:", reply_markup=cancel())

@router.message(Tool.waiting_watermark_text)
async def do_watermark(m: Message, state: FSMContext):
    data = await state.get_data()
    src = Path(data["src"])
    try:
        async with _sem:
            out, _ = await asyncio.to_thread(imaging.watermark, src, m.text[:60], cfg.temp_dir)
        await m.answer_document(out.open("rb"), filename=out.name)
    except Exception:
        log.exception("watermark failed")
        await m.answer("❌ Watermark failed.")
    finally:
        files.cleanup(src, locals().get('out'))
        await state.clear()

@router.callback_query(Tool.choosing, F.data.startswith("tool:"))
async def do_transform(cb: CallbackQuery, state: FSMContext):
    op = cb.data.split(":", 1)[1]
    data = await state.get_data()
    src = Path(data["src"])
    try:
        async with _sem:
            out, _ = await asyncio.to_thread(imaging.transform, src, op, cfg.temp_dir)
        await cb.message.answer_document(out.open("rb"), filename=out.name)
    except Exception:
        log.exception("transform failed")
        await cb.message.answer("❌ Operation failed.")
    finally:
        files.cleanup(src, locals().get('out'))
        await state.clear()

# Compress flow
@router.callback_query(F.data == "menu:compress")
async def compress_start(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Send an image first.", show_alert=True)
        return
    await cb.message.answer("Choose JPEG quality:", reply_markup=compress_quality())

@router.callback_query(F.data.startswith("cmp:"))
async def compress_do(cb: CallbackQuery, state: FSMContext):
    q = int(cb.data.split(":")[1])
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Session expired; resend image.", show_alert=True); return
    src = Path(data["src"])
    try:
        async with _sem:
            out, size_out = await asyncio.to_thread(imaging.compress, src, q, cfg.temp_dir)
        await cb.message.answer_document(
            out.open("rb"), filename=out.name,
            caption=f"✅ {data['bytes_in']/1024:.1f} KB → {size_out/1024:.1f} KB",
        )
        await record_conversion(cb.from_user.id, data["src_fmt"], "JPG",
                                data["bytes_in"], size_out, True)
    except Exception:
        log.exception("compress failed")
        await cb.message.answer("❌ Compression failed.")
    finally:
        files.cleanup(src, locals().get('out'))
        await state.clear()

# Resize flow
@router.callback_query(F.data == "menu:resize")
async def resize_start(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Send an image first.", show_alert=True); return
    await cb.message.answer("Target width (aspect ratio preserved):",
                            reply_markup=resize_presets())

@router.callback_query(F.data.startswith("rsz:"))
async def resize_do(cb: CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    if parts[1] == "custom":
        await cb.message.answer("Send width as a number (e.g. 1024):",
                                reply_markup=cancel())
        # User just types the number; handled below by text handler on generic state
        return
    await _do_resize(cb.from_user.id, cb.message, int(parts[1]), state)

async def _do_resize(user_id, message, width, state):
    data = await state.get_data()
    if "src" not in data:
        await message.answer("Session expired."); return
    src = Path(data["src"])
    try:
        async with _sem:
            out, size_out = await asyncio.to_thread(
                imaging.resize, src, width, None, True, cfg.temp_dir
            )
        await message.answer_document(out.open("rb"), filename=out.name)
        await record_conversion(user_id, data["src_fmt"], "PNG",
                                data["bytes_in"], size_out, True)
    except Exception:
        log.exception("resize failed")
        await message.answer("❌ Resize failed.")
    finally:
        files.cleanup(src, locals().get('out'))
        await state.clear()
