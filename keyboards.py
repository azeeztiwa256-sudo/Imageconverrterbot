from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu():
    rows = [
        [InlineKeyboardButton(text="🔄 Convert", callback_data="menu:convert"),
         InlineKeyboardButton(text="🗜 Compress", callback_data="menu:compress")],
        [InlineKeyboardButton(text="📐 Resize", callback_data="menu:resize"),
         InlineKeyboardButton(text="🎨 Tools",   callback_data="menu:tools")],
        [InlineKeyboardButton(text="📄 Batch → PDF", callback_data="menu:pdf")],
        [InlineKeyboardButton(text="ℹ️ Help", callback_data="menu:help")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def format_choice():
    fmts = ["JPG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "ICO"]
    rows, row = [], []
    for f in fmts:
        row.append(InlineKeyboardButton(text=f, callback_data=f"conv:{f}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def tools_menu():
    rows = [
        [InlineKeyboardButton(text="Grayscale", callback_data="tool:grayscale"),
         InlineKeyboardButton(text="B&W",       callback_data="tool:bw")],
        [InlineKeyboardButton(text="Rotate 90°", callback_data="tool:rotate90"),
         InlineKeyboardButton(text="Rotate 180°", callback_data="tool:rotate180")],
        [InlineKeyboardButton(text="Flip H",    callback_data="tool:flip_h"),
         InlineKeyboardButton(text="Flip V",    callback_data="tool:flip_v")],
        [InlineKeyboardButton(text="🖋 Watermark", callback_data="tool:watermark")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def compress_quality():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"{q}%", callback_data=f"cmp:{q}")
        for q in (90, 75, 60, 40)
    ]])

def resize_presets():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1280w", callback_data="rsz:1280:0"),
         InlineKeyboardButton(text="1920w", callback_data="rsz:1920:0"),
         InlineKeyboardButton(text="512w",  callback_data="rsz:512:0")],
        [InlineKeyboardButton(text="Custom", callback_data="rsz:custom")],
    ])

def cancel():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")
    ]])
