from __future__ import annotations
import io
from pathlib import Path
from PIL import Image, ImageOps, ImageDraw, ImageFont, ImageEnhance
import pillow_heif

pillow_heif.register_heif_opener()  # HEIC/HEIF read support

# Map "friendly" names → (Pillow format, save kwargs, extension)
OUTPUT_FORMATS = {
    "JPG":  ("JPEG", {"quality": 90, "optimize": True}, ".jpg"),
    "PNG":  ("PNG",  {"optimize": True},               ".png"),
    "WEBP": ("WEBP", {"quality": 90, "method": 4},     ".webp"),
    "GIF":  ("GIF",  {"optimize": True},               ".gif"),
    "BMP":  ("BMP",  {},                               ".bmp"),
    "TIFF": ("TIFF", {"compression": "tiff_lzw"},      ".tiff"),
    "ICO":  ("ICO",  {"sizes": [(256,256),(128,128),(64,64),(32,32),(16,16)]}, ".ico"),
}

def open_image(path: Path) -> Image.Image:
    im = Image.open(path)
    im.load()
    return im

def _flatten_for_jpeg(im: Image.Image) -> Image.Image:
    # JPEG can't hold alpha; composite on white
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        bg = Image.new("RGB", im.size, (255, 255, 255))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        return bg
    if im.mode not in ("RGB", "L"):
        return im.convert("RGB")
    return im

def convert(src: Path, dst_fmt: str, out_dir: Path) -> tuple[Path, int]:
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

def compress(src: Path, quality: int, out_dir: Path) -> tuple[Path, int]:
    im = _flatten_for_jpeg(open_image(src))
    out = out_dir / (src.stem + f"_q{quality}.jpg")
    im.save(out, format="JPEG", quality=quality, optimize=True)
    return out, out.stat().st_size

def resize(src: Path, w: int | None, h: int | None, keep_ratio: bool, out_dir: Path):
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

def transform(src: Path, op: str, out_dir: Path):
    """op ∈ {grayscale, bw, rotate90, rotate180, rotate270, flip_h, flip_v}"""
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

def watermark(src: Path, text: str, out_dir: Path):
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

def to_pdf(paths: list[Path], out_path: Path):
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

def metadata(src: Path) -> dict:
    im = Image.open(src)
    return {
        "format": im.format, "mode": im.mode, "size": im.size,
        "info_keys": sorted(k for k in im.info.keys()),
    }
