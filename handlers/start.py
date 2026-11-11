from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from keyboards import main_menu
from db import upsert_user

router = Router()

WELCOME = (
    "👋 <b>Image Converter Bot</b>\n\n"
    "I can convert, compress, resize, and edit images — plus batch → PDF.\n\n"
    "📎 Just send me an image to begin, or pick an option below."
)

@router.message(Command("start"))
async def start(m: Message):
    await upsert_user(m.from_user.id, m.from_user.username)
    await m.answer(WELCOME, reply_markup=main_menu())

@router.message(Command("help"))
async def help_cmd(m: Message):
    await m.answer(
        "Send an image → choose an output format → I'll send it back.\n\n"
        "Commands:\n"
        "/start — main menu\n"
        "/help — this message\n"
        "/about — about the bot"
    )

@router.message(Command("about"))
async def about(m: Message):
    await m.answer("Built with aiogram + Pillow. Source is open for auditing.")
