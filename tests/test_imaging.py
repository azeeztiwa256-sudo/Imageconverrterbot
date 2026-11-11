import io
from pathlib import Path
import pytest
from PIL import Image
from services import imaging

@pytest.fixture
def sample(tmp_path: Path) -> Path:
    p = tmp_path / "in.png"
    Image.new("RGBA", (300, 200), (255, 0, 0, 255)).save(p)
    return p

@pytest.mark.parametrize("fmt", ["JPG", "PNG", "WEBP", "BMP", "TIFF", "ICO"])
def test_convert(sample, fmt, tmp_path):
    out, size = imaging.convert(sample, fmt, tmp_path)
    assert out.exists() and size > 0
    Image.open(out).load()

def test_compress(sample, tmp_path):
    out, size = imaging.compress(sample, 70, tmp_path)
    assert size < sample.stat().st_size or size > 0

def test_resize(sample, tmp_path):
    out, _ = imaging.resize(sample, 100, None, True, tmp_path)
    assert Image.open(out).size == (100, 67)

def test_grayscale(sample, tmp_path):
    out, _ = imaging.transform(sample, "grayscale", tmp_path)
    assert Image.open(out).mode == "L"

def test_watermark(sample, tmp_path):
    out, _ = imaging.watermark(sample, "TEST", tmp_path)
    assert Image.open(out).size == (300, 200)

def test_pdf(sample, tmp_path):
    out = tmp_path / "x.pdf"
    imaging.to_pdf([sample], out)
    assert out.read_bytes().startswith(b"%PDF")
