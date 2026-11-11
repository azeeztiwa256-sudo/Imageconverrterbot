from pathlib import Path
import asyncio
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import cfg
from services import files, imaging
from utils import log

router = Router()
_sem = asyncio.Semaphore(2)

class Batch(StatesGroup):
    collecting = State()

@router.message(F.text == "/pdf")
async def pdf_start(m: Message, state: FSMContext):
    await state.set_state(Batch.collecting)
    await state.update_data(paths=[])
    await m.answer(f"Send up to {cfg.max_batch} images, then /done.")

@router.message(Batch.collecting, F.photo | F.document)
async def pdf_collect(m: Message, state: FSMContext):
    data = await state.get_data()
    paths: list[str] = data.get("paths", [])
    if len(paths) >= cfg.max_batch:
        await m.answer(f"Maximum {cfg.max_batch} reached. /done to build PDF.")
        return
    tg = m.photo[-1] if m.photo else m.document
    f = await m.bot.get_file(tg.file_id)
    suffix = Path(f.file_path or "x").suffix or ".img"
    local = files.temp_path(suffix)
    await m.bot.download_file(f.file_path, destination=local)
    if not files.sniff_image(local):
        files.cleanup(local); await m.answer("Not a valid image, skipped."); return
    paths.append(str(local))
    await state.update_data(paths=paths)
    await m.answer(f"Added ({len(paths)}/{cfg.max_batch}). /done to finish.")

@router.message(Batch.collecting, F.text == "/done")
async def pdf_build(m: Message, state: FSMContext):
    data = await state.get_data()
    paths = [Path(p) for p in data.get("paths", [])]
    await state.clear()
    if not paths:
        await m.answer("No images collected."); return
    out = files.temp_path(".pdf")
    try:
        async with _sem:
            await asyncio.to_thread(imaging.to_pdf, paths, out)
        await m.answer_document(out.open("rb"), filename="images.pdf")
    except Exception:
        log.exception("pdf build failed")
        await m.answer("❌ Could not build PDF.")
    finally:
        files.cleanup(*paths, out)
