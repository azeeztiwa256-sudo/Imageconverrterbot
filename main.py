import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

import aiosqlite
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile
)
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv
from PIL import Image, ImageOps, UnidentifiedImageError

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except Exception:
    pass

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "").strip()
ADMIN_IDS = {
    int(x.strip()) for x in ADMIN_IDS_RAW.split(",")
    if x.strip().isdigit()
}
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "20"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
MAX_PIXELS = int(os.getenv("MAX_PIXELS", "40000000"))
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
CONCURRENCY = max(1, int(os.getenv("CONCURRENCY", "3")))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("image-converter")
dp = Dispatcher()
semaphore = asyncio.Semaphore(CONCURRENCY)

FORMATS = {
    "jpg": ("JPEG", "JPG"),
    "png": ("PNG", "PNG"),
    "webp": ("WEBP", "WEBP"),
    "bmp": ("BMP", "BMP"),
    "tiff": ("TIFF", "TIFF"),
    "gif": ("GIF", "GIF"),
    "ico": ("ICO", "ICO"),
}
# In-memory pending conversions: callback token -> source file path, user id, filename
pending = {}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🖼 Convert to JPG", callback_data="fmt:jpg"),
            InlineKeyboardButton(text="🧩 Convert to PNG", callback_data="fmt:png"),
        ],
        [
            InlineKeyboardButton(text="🌐 Convert to WEBP", callback_data="fmt:webp"),
            InlineKeyboardButton(text="📷 Convert to BMP", callback_data="fmt:bmp"),
        ],
        [
            InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="fmt:gif"),
            InlineKeyboardButton(text="📄 Convert to TIFF", callback_data="fmt:tiff"),
        ],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="help")]
    ])


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_seen TEXT DEFAULT CURRENT_TIMESTAMP,
                conversions INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                input_format TEXT,
                output_format TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def register_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,)
        )
        await db.commit()


async def record_conversion(user_id: int, input_format: str, output_format: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users(user_id) VALUES(?)", (user_id,)
        )
        await db.execute(
            "UPDATE users SET conversions = conversions + 1 WHERE user_id = ?",
            (user_id,)
        )
        await db.execute(
            "INSERT INTO events(user_id, input_format, output_format) VALUES(?,?,?)",
            (user_id, input_format, output_format)
        )
        await db.commit()


async def get_image_source(message: Message):
    """Return Telegram file object and original filename for photo/document uploads."""
    if message.photo:
        return message.photo[-1], "telegram_photo.jpg"
    doc = message.document
    if doc:
        mime = (doc.mime_type or "").lower()
        name = doc.file_name or "uploaded_image"
        allowed_mimes = (
            "image/", "application/octet-stream"
        )
        if not (mime.startswith("image/") or mime == "application/octet-stream"):
            raise ValueError("Please send an image file, not another document type.")
        return doc, name
    raise ValueError("Please send an image as a photo or document.")


def safe_output_name(original: str, ext: str) -> str:
    stem = Path(original).stem[:60] or "converted"
    clean = "".join(c for c in stem if c.isalnum() or c in ("-", "_")) or "image"
    return f"{clean}.{ext}"


def convert_image(source: Path, destination: Path, target: str):
    fmt, ext = FORMATS[target]
    with Image.open(source) as opened:
        if getattr(opened, "width", 0) * getattr(opened, "height", 0) > MAX_PIXELS:
            raise ValueError("This image has too many pixels to process safely.")
        image = ImageOps.exif_transpose(opened)

        if target == "gif":
            # Preserve animation when the source is animated; otherwise create a static GIF.
            frames = []
            durations = []
            try:
                for frame in ImageSequence.Iterator(image):
                    frames.append(frame.convert("RGBA"))
                    durations.append(image.info.get("duration", 100))
            except Exception:
                frames = []
            if frames:
                frames[0].save(
                    destination, format="GIF", save_all=True,
                    append_images=frames[1:], duration=durations[0],
                    loop=0, optimize=True
                )
            else:
                image.convert("RGB").save(destination, format="GIF")
            return

        if target == "ico":
            image = image.convert("RGBA")
            image.thumbnail((256, 256))
            image.save(destination, format="ICO", sizes=[(image.width, image.height)])
            return

        if fmt == "JPEG":
            if image.mode not in ("RGB", "L"):
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image.convert("RGB"))
                image = background
            image.save(destination, format=fmt, quality=92, optimize=True)
        elif fmt == "WEBP":
            image.save(destination, format=fmt, quality=92, method=4)
        else:
            image.save(destination, format=fmt)


# Imported separately to keep GIF handling explicit.
from PIL import ImageSequence


@dp.message(CommandStart())
async def start(message: Message):
    await register_user(message.from_user.id)
    await message.answer(
        "👋 <b>Welcome to Image Converter Bot!</b>\n\n"
        "Send me an image as a photo or file, then choose the format you want.\n\n"
        f"📦 Maximum upload size: {MAX_FILE_MB} MB\n"
        "🛠 Supported outputs: JPG, PNG, WEBP, BMP, GIF, TIFF and ICO.",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


@dp.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "Send an image as a Telegram photo or document. I will show conversion options.\n\n"
        "For best quality, send the image as a document. Some formats may not preserve "
        "animation, transparency, or metadata exactly."
    )


@dp.message(Command("stats"))
async def stats(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("This command is for administrators only.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        users = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) FROM events")
        conversions = (await cur.fetchone())[0]
    await message.answer(
        f"📊 <b>Bot statistics</b>\nUsers: {users}\nConversions: {conversions}",
        parse_mode="HTML"
    )


@dp.message(F.photo | F.document)
async def receive_image(message: Message, bot: Bot):
    user_id = message.from_user.id
    await register_user(user_id)
    status = await message.answer("⏳ Checking your image...")
    temp_dir = Path(tempfile.gettempdir()) / "image_converter_bot"
    temp_dir.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    source_path = temp_dir / f"{token}_source"
    try:
        file_obj, original_name = await get_image_source(message)
        if getattr(file_obj, "file_size", 0) and file_obj.file_size > MAX_FILE_BYTES:
            raise ValueError(f"File is larger than the {MAX_FILE_MB} MB limit.")
        telegram_file = await bot.get_file(file_obj.file_id)
        await bot.download_file(telegram_file.file_path, destination=source_path)
        if source_path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File is larger than the {MAX_FILE_MB} MB limit.")
        # Verify content by opening it, rather than trusting the filename or MIME type.
        with Image.open(source_path) as img:
            img.verify()
            input_format = img.format or "Unknown"
        pending[token] = {
            "path": str(source_path),
            "user_id": user_id,
            "name": original_name,
            "input_format": input_format
        }
        await status.edit_text(
            f"✅ Image received ({input_format}, {source_path.stat().st_size // 1024} KB).\n"
            "Choose an output format:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="JPG", callback_data=f"convert:{token}:jpg"),
                    InlineKeyboardButton(text="PNG", callback_data=f"convert:{token}:png"),
                    InlineKeyboardButton(text="WEBP", callback_data=f"convert:{token}:webp"),
                ],
                [
                    InlineKeyboardButton(text="BMP", callback_data=f"convert:{token}:bmp"),
                    InlineKeyboardButton(text="GIF", callback_data=f"convert:{token}:gif"),
                    InlineKeyboardButton(text="TIFF", callback_data=f"convert:{token}:tiff"),
                ],
                [InlineKeyboardButton(text="ICO", callback_data=f"convert:{token}:ico")]
            ])
        )
    except (UnidentifiedImageError, OSError):
        source_path.unlink(missing_ok=True)
        await status.edit_text("❌ I couldn't read that image. Please try another supported image.")
    except Exception as exc:
        source_path.unlink(missing_ok=True)
        await status.edit_text(f"❌ {str(exc)[:250]}")


@dp.callback_query(F.data.startswith("convert:"))
async def do_conversion(callback: CallbackQuery, bot: Bot):
    try:
        _, token, target = callback.data.split(":")
        item = pending.get(token)
        if not item:
            await callback.answer("This upload has expired. Please send the image again.", show_alert=True)
            return
        if item["user_id"] != callback.from_user.id:
            await callback.answer("This conversion belongs to another user.", show_alert=True)
            return
        if target not in FORMATS:
            await callback.answer("Unsupported output format.", show_alert=True)
            return

        await callback.answer("Conversion started...")
        await callback.message.edit_text(f"⚙️ Converting your image to {target.upper()}...")
        source = Path(item["path"])
        output = source.parent / f"{token}_output.{target}"
        async with semaphore:
            await asyncio.to_thread(convert_image, source, output, target)

        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=FSInputFile(output, filename=safe_output_name(item["name"], target)),
            caption=f"✅ Conversion complete!\nFormat: {target.upper()}\n"
                    f"File size: {output.stat().st_size // 1024} KB"
        )
        await record_conversion(
            callback.from_user.id, item["input_format"], target.upper()
        )
        await callback.message.edit_text("🎉 Done! Send another image whenever you're ready.")
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        pending.pop(token, None)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        await callback.message.answer(f"❌ Conversion failed: {str(exc)[:200]}")
    except Exception:
        log.exception("Unexpected conversion failure")
        await callback.message.answer("❌ Something went wrong while converting your image. Please try again.")
    finally:
        # The source is removed after success; retain it on a recoverable failure only
        # until the user retries or the process restarts.


@dp.callback_query(F.data == "help")
async def help_callback(callback: CallbackQuery):
    await callback.answer()
    await callback.message.answer(
        "Send an image as a photo or document, then choose an output format.\n"
        "For best quality, send it as a document."
    )


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set. Add your Telegram bot token to the hosting environment.")
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    log.info("Starting Image Converter Bot...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped.")
