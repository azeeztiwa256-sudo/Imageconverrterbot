import os
import re
import uuid
from pathlib import Path
from PIL import Image
from config import cfg

SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

def safe_name(name: str, fallback: str = "file") -> str:
    name = SAFE_NAME.sub("_", (name or "").strip())[:100]
    return name or fallback

def temp_path(suffix: str) -> Path:
    return cfg.temp_dir / f"{uuid.uuid4().hex}{suffix}"

def sniff_image(path: Path) -> str | None:
    """Verify by content, not extension. Returns Pillow format or None."""
    try:
        with Image.open(path) as im:
            return im.format
    except Exception:
        return None

def cleanup(*paths: Path):
    for p in paths:
        try:
            if p and p.exists():
                os.unlink(p)
        except OSError:
            pass
