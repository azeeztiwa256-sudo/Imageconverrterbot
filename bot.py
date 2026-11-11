"""
Image Converter Bot — single-file build (aiogram 3 + Pillow).

Layout merged into one file for easy upload:
  - config / logging
  - SQLite persistence (users, conversions, daily limits, kv)
  - inline keyboards
  - image processing (Pillow, run in worker threads)
  - Telegram handlers: start, convert, compress, resize, tools, batch->PDF, admin
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass


# =====================================================================
# CONFIG
# =====================================================================

load_dotenv()


def _ids(raw: str) -> list[int]:
    return [int(x) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]


@dataclass(frozen=True)
class Config:
    token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: list[int] = field(default_factory=lambda: _ids(os.getenv("ADMIN_IDS", "")))
    max_file_mb: int = int(os.getenv("MAX_FILE_MB", "20"))
    max_batch: int = int(os.getenv("MAX_BATCH", "10"))
    daily_limit: int = int(os.getenv("DAILY_FREE_LIMIT", "100"))
    temp_dir: Path = Path(os.getenv("TEMP_DIR", "/tmp/imageconv"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def validate(self) -> None:
        if not self.token or ":" not in self.token:
            raise SystemExit("BOT_TOKEN is not set or invalid")
        if not self.admin_ids:
            raise SystemExit("ADMIN_IDS is not set")
        self.temp_dir.mkdir(parents=True, exist_ok=True)


cfg = Config()
cfg.validate()


# =====================================================================
# LOGGING
# =====================================================================

logging.basicConfig(
    level=cfg.log_level,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logging.getLogger("aiogram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
log = logging.getLogger("imageconv")


# =====================================================================
# DATABASE
# =====================================================================

DB_PATH = os.getenv("DB_PATH", "bot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    src_fmt      TEXT,
    dst_fmt      TEXT,
    bytes_in     INTEGER,
    bytes_out    INTEGER,
    ok           INTEGER NOT NULL,
    ts           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_usage (
    user_id      INTEGER NOT NULL,
    day          TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_user(user_id: int, username: str | None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users(user_id, username, first_seen, last_seen)
               VALUES(?,?,datetime('now'),datetime('now'))
               ON CONFLICT(user_id) DO UPDATE
                 SET last_seen=datetime('now'), username=excluded.username""",
            (user_id, username),
        )
        await db.commit()


async def record_conversion(
    user_id: int, src: str | None, dst: str | None,
    size_in: int, size_out: int, ok: bool,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO conversions
               (user_id, src_fmt, dst_fmt, bytes_in, bytes_out, ok, ts)
               VALUES (?,?,?,?,?,?,datetime('now'))""",
            (user_id, src, dst, size_in, size_out, int(ok)),
        )
        await db.commit()


async def bump_daily(user_id: int) -> int:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_usage(user_id, day, count) VALUES(?,?,1)
               ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1""",
            (user_id, today),
        )
        await db.commit()
        async with db.execute(
            "SELECT count FROM daily_usage WHERE user_id=? AND day=?",
            (user_id, today),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def set_kv(k: str, v: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
        await db.commit()


async def get_kv(k: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT v FROM kv WHERE k=?", (k,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default


async def stats_snapshot() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async def one(q: str, *p):
            async with db.execute(q, p) as c:
                r = await c.fetchone()
                return r[0] if r else 0

        return {
            "users":       await one("SELECT COUNT(*) FROM users"),
            "conversions": await one("SELECT COUNT(*) FROM conversions WHERE ok=1"),
            "failures":    await one("SELECT COUNT(*) FROM conversions WHERE ok=0"),
            "today":       await one("SELECT COUNT(*) FROM conversions WHERE ok=1 AND date(ts)=date('now')"),
            "top_dst":     await one("SELECT dst_fmt FROM conversions WHERE ok=1 GROUP BY dst_fmt ORDER BY COUNT(*) DESC LIMIT 1") or "-",
        }


# =====================================================================
# FILE HELPERS
# =====================================================================

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str, fallback: str = "file") -> str:
    name = SAFE_NAME.sub("_", (name or "").strip())[:100]
    return name or fallback


def temp_path(suffix: str) -> Path:
    return cfg.temp_dir / f"{uuid.uuid4().hex}{suffix}"


def sniff_image(path: Path) -> str | None:
    try:
        with Image.open(path) as im:
            im.verify()
            return im.format
    except Exception:
        return None


def cleanup(*paths: Path | None) -> None:
    for p in paths:
        if not p:
            continue
        try:
            if Path(p).exists():
                os.unlink(p)
        except OSError:
            pass


# =====================================================================
# IMAGE PROCESSING
# =====================================================================

OUTPUT_FORMATS = {
    "JPG":  ("JPEG", {"quality": 90, "optimize": True}, ".jpg"),
    "PNG":  ("PNG",  {"optimize": True},               ".png"),
    "WEBP": ("WEBP", {"quality": 90, "method": 4},     ".webp"),
    "GIF":  ("GIF",  {"optimize": True},               ".gif"),
    "BMP":  ("BMP",  {},                               ".bmp"),
    "TIFF": ("TIFF", {"compression": "tiff_lzw"},      ".tiff"),
    "ICO":  ("ICO",  {"sizes": [(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)]}, ".ico"),
}


def open_image(path: Path) -> Image.Image:
    im = Image.open(path)
    im.load()
    return im


def _flatten_for_jpeg(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        return bg
    if im.mode not in ("RGB", "L"):
        return im.convert("RGB")
    return im


def convert_image(src: Path, dst_fmt: str, out_dir: Path) -> tuple[Path, int]:
    if dst_fmt not in OUTPUT_FORMATS:
        raise ValueError(f"unsupported target format {dst_fmt}")
    fmt, save_kw, ext = OUTPUT_FORMATS[dst_fmt]
    im = open_image(src)

    if fmt == "JPEG":
        im = _flatten_for_jpeg(im)
    elif fmt == "GIF":
        im = im.convert("P", palette=Image.Palette.ADAPTIVE)
    elif fmt == "ICO":
        im = im.convert("RGBA")
    elif im.mode == "P":
        im = im.convert("RGBA")

    out = out_dir / (src.stem + ext)
    im.save(out, format=fmt, **save_kw)
    return out, out.stat().st_size


def compress_image(src: Path, quality: int, out_dir: Path) -> tuple[Path, int]:
    im = _flatten_for_jpeg(open_image(src))
    out = out_dir / (src.stem + f"_q{quality}.jpg")
    im.save(out, format="JPEG", quality=quality, optimize=True)
    return out, out.stat().st_size


def resize_image(src: Path, w: int | None, h: int | None, keep_ratio: bool,
                 out_dir: Path) -> tuple[Path, int]:
    im = open_image(src)
    orig = im.size
    if keep_ratio:
        if w and not h:
            h = round(orig[1] * w / orig[0])
        elif h and not w:
            w = round(orig[0] * h / orig[1])
    new = (max(1, w or orig[0]), max(1, h or orig[1]))
    im = im.resize(new, Image.LANCZOS)
    out = out_dir / (src.stem + f"_{new[0]}x{new[1]}.png")
    im.save(out, format="PNG", optimize=True)
    return out, out.stat().st_size


def transform_image(src: Path, op: str, out_dir: Path) -> tuple[Path, int]:
    im = open_image(src)
    if op == "grayscale":
        im = ImageOps.grayscale(im)
    elif op == "bw":
        im = ImageOps.grayscale(im).point(lambda p: 255 if p > 128 else 0)
    elif op.startswith("rotate"):
        im = im.rotate(int(op[6:]), expand=True)
    elif op == "flip_h":
        im = ImageOps.mirror(im)
    elif op == "flip_v":
        im = ImageOps.flip(im)
    else:
        raise ValueError(f"unknown op {op}")
    out = out_dir / (src.stem + f"_{op}.png")
    im.save(out, format="PNG", optimize=True)
    return out, out.stat().st_size


def watermark_image(src: Path, text: str, out_dir: Path) -> tuple[Path, int]:
    im = open_image(src).convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(16, im.size[0] // 25))
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = 12
    x, y = im.size[0] - tw - pad, im.size[1] - th - pad
    draw.rectangle([x - pad, y - pad, x + tw + pad, y + th + pad], fill=(0, 0, 0, 130))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 230))
    out = out_dir / (src.stem + "_watermark.png")
    Image.alpha_composite(im, layer).convert("RGB").save(out, format="PNG", optimize=True)
    return out, out.stat().st_size


def images_to_pdf(paths: list[Path], out_path: Path) -> tuple[Path, int]:
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    c = canvas.Canvas(str(out_path))
    for p in paths:
        im = open_image(p)
        w, h = im.size
        c.setPageSize((w, h))
        c.drawImage(ImageReader(im), 0, 0, w, h)
        c.showPage()
    c.save()
    return out_path, out_path.stat().st_size


# =====================================================================
# KEYBOARDS
# =====================================================================

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Convert",  callback_data="menu:convert"),
         InlineKeyboardButton(text="🗜 Compress", callback_data="menu:compress")],
        [InlineKeyboardButton(text="📐 Resize",   callback_data="menu:resize"),
         InlineKeyboardButton(text="🎨 Tools",    callback_data="menu:tools")],
        [InlineKeyboardButton(text="📄 Batch → PDF", callback_data="menu:pdf")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="menu:help")],
    ])


def format_choice() -> InlineKeyboardMarkup:
    fmts = ["JPG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "ICO"]
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for f in fmts:
        row.append(InlineKeyboardButton(text=f, callback_data=f"conv:{f}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tools_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Grayscale",  callback_data="tool:grayscale"),
         InlineKeyboardButton(text="B&W",        callback_data="tool:bw")],
        [InlineKeyboardButton(text="Rotate 90°", callback_data="tool:rotate90"),
         InlineKeyboardButton(text="Rotate 180°",callback_data="tool:rotate180")],
        [InlineKeyboardButton(text="Flip H",     callback_data="tool:flip_h"),
         InlineKeyboardButton(text="Flip V",     callback_data="tool:flip_v")],
        [InlineKeyboardButton(text="🖋 Watermark", callback_data="tool:watermark")],
    ])


def compress_quality() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{q}%", callback_data=f"cmp:{q}")
        for q in (90, 75, 60, 40)
    ]])


def resize_presets() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1280w", callback_data="rsz:1280"),
         InlineKeyboardButton(text="1920w", callback_data="rsz:1920"),
         InlineKeyboardButton(text="512w",  callback_data="rsz:512")],
    ])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")
    ]])


# =====================================================================
# HANDLERS
# =====================================================================

router = Router()
_sem = asyncio.Semaphore(4)


class Conv(StatesGroup):
    awaiting_format = State()


class Tool(StatesGroup):
    choosing = State()
    waiting_watermark_text = State()


class Batch(StatesGroup):
    collecting = State()


# ---------- /start, /help, /about ----------

WELCOME = (
    "👋 <b>Image Converter Bot</b>\n\n"
    "I can convert, compress, resize, and edit images — plus batch → PDF.\n\n"
    "📎 Send me an image to begin, or pick an option below."
)


@router.message(Command("start"))
async def cmd_start(m: Message) -> None:
    await upsert_user(m.from_user.id, m.from_user.username)
    await m.answer(WELCOME, reply_markup=main_menu())


@router.message(Command("help"))
async def cmd_help(m: Message) -> None:
    await m.answer(
        "Send an image → choose an output format → I'll send it back.\n\n"
        "Commands:\n"
        "/start — main menu\n"
        "/help — this message\n"
        "/about — about the bot\n"
        "/pdf — batch images into a PDF (then /done)"
    )


@router.message(Command("about"))
async def cmd_about(m: Message) -> None:
    await m.answer("Built with aiogram 3 + Pillow. Source is open for auditing.")


@router.callback_query(F.data == "menu:help")
async def menu_help(cb: CallbackQuery) -> None:
    await cb.message.answer(
        "Send me an image → I'll ask which format → tap one → I return the file.\n"
        "Use /pdf for batch → PDF."
    )
    await cb.answer()


@router.callback_query(F.data == "menu:convert")
async def menu_convert(cb: CallbackQuery) -> None:
    await cb.answer("Send an image to start converting.", show_alert=True)


@router.callback_query(F.data == "menu:pdf")
async def menu_pdf(cb: CallbackQuery) -> None:
    await cb.message.answer("Send /pdf to start a batch → PDF session.")
    await cb.answer()


# ---------- image intake → format choice ----------

@router.message(F.photo | F.document)
async def receive_image(m: Message, state: FSMContext) -> None:
    await upsert_user(m.from_user.id, m.from_user.username)

    used = await bump_daily(m.from_user.id)
    if used > cfg.daily_limit and m.from_user.id not in cfg.admin_ids:
        await m.answer(f"⚠️ Daily limit ({cfg.daily_limit}) reached. Try again tomorrow.")
        return

    tg_file = None
    if m.photo:
        tg_file = await m.bot.get_file(m.photo[-1].file_id)
    elif m.document and (m.document.mime_type or "").startswith("image/"):
        tg_file = await m.bot.get_file(m.document.file_id)
    else:
        await m.answer("Send an image (photo or image file).")
        return

    size_mb = (tg_file.file_size or 0) / 1024 / 1024
    if size_mb > cfg.max_file_mb:
        await m.answer(f"❌ File too large ({size_mb:.1f} MB). Limit: {cfg.max_file_mb} MB.")
        return

    suffix = Path(tg_file.file_path or "x").suffix or ".img"
    local = temp_path(suffix)
    await m.bot.download_file(tg_file.file_path, destination=local)

    fmt = await asyncio.to_thread(sniff_image, local)
    if not fmt:
        cleanup(local)
        await m.answer("❌ That doesn't look like a valid image.")
        return

    await state.set_state(Conv.awaiting_format)
    await state.update_data(
        src=str(local),
        src_fmt=fmt,
        bytes_in=local.stat().st_size,
    )
    await m.answer(
        f"✅ Detected <b>{fmt}</b> ({local.stat().st_size / 1024:.1f} KB).\n"
        f"Choose output format:",
        reply_markup=format_choice(),
    )


# ---------- convert ----------

@router.callback_query(Conv.awaiting_format, F.data.startswith("conv:"))
async def do_convert(cb: CallbackQuery, state: FSMContext) -> None:
    dst = cb.data.split(":", 1)[1]
    data = await state.get_data()
    src = Path(data["src"])
    await state.clear()

    await cb.message.edit_text(f"⏳ Converting to {dst}…")
    out = None
    try:
        async with _sem:
            out, size_out = await asyncio.to_thread(convert_image, src, dst, cfg.temp_dir)
        await cb.message.answer_document(
            out.open("rb"),
            filename=out.name,
            caption=f"✅ {data['src_fmt']} → {dst}\n{size_out / 1024:.1f} KB",
        )
        await record_conversion(
            cb.from_user.id, data["src_fmt"], dst, data["bytes_in"], size_out, True,
        )
    except Exception:
        log.exception("convert failed")
        await cb.message.answer("❌ Conversion failed. The file may be corrupted.")
        await record_conversion(cb.from_user.id, data.get("src_fmt"), dst, 0, 0, False)
    finally:
        cleanup(src, out)


@router.callback_query(F.data == "cancel")
async def do_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "src" in data:
        cleanup(Path(data["src"]))
    await state.clear()
    await cb.message.edit_text("Cancelled.")


# ---------- compress ----------

@router.callback_query(F.data == "menu:compress")
async def compress_start(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Send an image first.", show_alert=True)
        return
    await cb.message.answer("Choose JPEG quality:", reply_markup=compress_quality())


@router.callback_query(F.data.startswith("cmp:"))
async def compress_do(cb: CallbackQuery, state: FSMContext) -> None:
    q = int(cb.data.split(":")[1])
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Session expired; resend image.", show_alert=True)
        return
    src = Path(data["src"])
    out = None
    try:
        async with _sem:
            out, size_out = await asyncio.to_thread(compress_image, src, q, cfg.temp_dir)
        await cb.message.answer_document(
            out.open("rb"),
            filename=out.name,
            caption=f"✅ {data['bytes_in'] / 1024:.1f} KB → {size_out / 1024:.1f} KB",
        )
        await record_conversion(
            cb.from_user.id, data["src_fmt"], "JPG", data["bytes_in"], size_out, True,
        )
    except Exception:
        log.exception("compress failed")
        await cb.message.answer("❌ Compression failed.")
    finally:
        cleanup(src, out)
        await state.clear()


# ---------- resize ----------

@router.callback_query(F.data == "menu:resize")
async def resize_start(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Send an image first.", show_alert=True)
        return
    await cb.message.answer(
        "Target width (aspect ratio preserved):", reply_markup=resize_presets(),
    )


@router.callback_query(F.data.startswith("rsz:"))
async def resize_do(cb: CallbackQuery, state: FSMContext) -> None:
    width = int(cb.data.split(":")[1])
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Session expired.", show_alert=True)
        return
    src = Path(data["src"])
    out = None
    try:
        async with _sem:
            out, size_out = await asyncio.to_thread(
                resize_image, src, width, None, True, cfg.temp_dir
            )
        await cb.message.answer_document(out.open("rb"), filename=out.name)
        await record_conversion(
            cb.from_user.id, data["src_fmt"], "PNG", data["bytes_in"], size_out, True,
        )
    except Exception:
        log.exception("resize failed")
        await cb.message.answer("❌ Resize failed.")
    finally:
        cleanup(src, out)
        await state.clear()


# ---------- tools ----------

@router.callback_query(F.data == "menu:tools")
async def show_tools(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if "src" not in data:
        await cb.answer("Send an image first, then choose a tool.", show_alert=True)
        return
    await state.set_state(Tool.choosing)
    await cb.message.answer("Pick a tool:", reply_markup=tools_menu())


@router.callback_query(Tool.choosing, F.data == "tool:watermark")
async def ask_watermark(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(Tool.waiting_watermark_text)
    await cb.message.answer("Send the watermark text:", reply_markup=cancel_kb())


@router.message(Tool.waiting_watermark_text)
async def do_watermark(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    src = Path(data["src"])
    out = None
    try:
        async with _sem:
            out, _ = await asyncio.to_thread(
                watermark_image, src, (m.text or "WATERMARK")[:60], cfg.temp_dir
            )
        await m.answer_document(out.open("rb"), filename=out.name)
    except Exception:
        log.exception("watermark failed")
        await m.answer("❌ Watermark failed.")
    finally:
        cleanup(src, out)
        await state.clear()


@router.callback_query(Tool.choosing, F.data.startswith("tool:"))
async def do_transform(cb: CallbackQuery, state: FSMContext) -> None:
    op = cb.data.split(":", 1)[1]
    data = await state.get_data()
    src = Path(data["src"])
    out = None
    try:
        async with _sem:
            out, _ = await asyncio.to_thread(transform_image, src, op, cfg.temp_dir)
        await cb.message.answer_document(out.open("rb"), filename=out.name)
    except Exception:
        log.exception("transform failed")
        await cb.message.answer("❌ Operation failed.")
    finally:
        cleanup(src, out)
        await state.clear()


# ---------- batch -> PDF ----------

@router.message(Command("pdf"))
async def pdf_start(m: Message, state: FSMContext) -> None:
    await state.set_state(Batch.collecting)
    await state.update_data(paths=[])
    await m.answer(
        f"Send up to {cfg.max_batch} images, then /done to build the PDF."
    )


@router.message(Batch.collecting, Command("done"))
async def pdf_build(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    paths = [Path(p) for p in data.get("paths", [])]
    await state.clear()
    if not paths:
        await m.answer("No images collected.")
        return
    out = temp_path(".pdf")
    try:
        async with _sem:
            await asyncio.to_thread(images_to_pdf, paths, out)
        await m.answer_document(out.open("rb"), filename="images.pdf")
    except Exception:
        log.exception("pdf build failed")
        await m.answer("❌ Could not build PDF.")
    finally:
        cleanup(*paths, out)


@router.message(Batch.collecting, F.photo | F.document)
async def pdf_collect(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    paths: list[str] = data.get("paths", [])
    if len(paths) >= cfg.max_batch:
        await m.answer(f"Maximum {cfg.max_batch} reached. /done to build PDF.")
        return

    tg = m.photo[-1] if m.photo else m.document
    tg_file = await m.bot.get_file(tg.file_id)
    suffix = Path(tg_file.file_path or "x").suffix or ".img"
    local = temp_path(suffix)
    await m.bot.download_file(tg_file.file_path, destination=local)

    if not await asyncio.to_thread(sniff_image, local):
        cleanup(local)
        await m.answer("Not a valid image, skipped.")
        return

    paths.append(str(local))
    await state.update_data(paths=paths)
    await m.answer(f"Added ({len(paths)}/{cfg.max_batch}). /done to finish.")


# ---------- admin ----------

def _is_admin(m: Message) -> bool:
    return m.from_user is not None and m.from_user.id in cfg.admin_ids


@router.message(Command("admin"))
async def cmd_admin(m: Message) -> None:
    if not _is_admin(m):
        return
    maint = await get_kv("maintenance", "off")
    await m.answer(
        "🛠 <b>Admin</b>\n"
        f"Maintenance: <b>{maint}</b>\n\n"
        "/stats — usage snapshot\n"
        "/broadcast &lt;text&gt; — announce\n"
        "/maintenance on|off"
    )


@router.message(Command("stats"))
async def cmd_stats(m: Message) -> None:
    if not _is_admin(m):
        return
    s = await stats_snapshot()
    await m.answer(
        f"👥 Users: {s['users']}\n"
        f"✅ Conversions: {s['conversions']}\n"
        f"❌ Failures: {s['failures']}\n"
        f"📅 Today: {s['today']}\n"
        f"🏆 Top target: {s['top_dst']}"
    )


@router.message(Command("maintenance"))
async def cmd_maintenance(m: Message) -> None:
    if not _is_admin(m):
        return
    parts = (m.text or "").split()
    if len(parts) != 2 or parts[1] not in ("on", "off"):
        await m.answer("Usage: /maintenance on|off")
        return
    await set_kv("maintenance", parts[1])
    await m.answer(f"Maintenance is now <b>{parts[1]}</b>.")


@router.message(Command("broadcast"))
async def cmd_broadcast(m: Message) -> None:
    if not _is_admin(m):
        return
    text = (m.text or "").partition(" ")[2].strip()
    if not text:
        await m.answer("Usage: /broadcast your message")
        return
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


# =====================================================================
# MIDDLEWARE + MAIN
# =====================================================================

async def maintenance_mw(handler, event, data):
    user = getattr(event, "from_user", None)
    if user and user.id in cfg.admin_ids:
        return await handler(event, data)
    if await get_kv("maintenance", "off") == "on":
        try:
            await event.answer("🛠 Bot is under maintenance. Please try later.")
        except Exception:
            pass
        return
    return await handler(event, data)


async def main() -> None:
    await init_db()

    bot = Bot(cfg.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(maintenance_mw)
    dp.callback_query.middleware(maintenance_mw)

    dp.include_router(router)

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
