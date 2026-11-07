"""OCR for posted schedules: images and PDF pages -> text, so the age-group rule can judge each page.

Used by fetch.py.  Needs tesseract (apt: tesseract-ocr) and pip: pytesseract pillow pymupdf.
Everything degrades gracefully: if OCR isn't available the page is kept and marked "unread".
"""
import re
import sys
from pathlib import Path

try:
    import pytesseract
    from PIL import Image, ImageOps
except ImportError:          # pragma: no cover
    pytesseract = None
try:
    import fitz              # pymupdf
except ImportError:          # pragma: no cover
    fitz = None


def _clean(t):
    return re.sub(r"[ \t]+", " ", t or "").strip()


def ocr_image(path):
    if not pytesseract:
        return None
    try:
        im = Image.open(path)
        im = ImageOps.exif_transpose(im).convert("L")
        if im.width < 1500:                      # upscale small phone screenshots; tesseract likes ~300dpi
            im = im.resize((im.width * 2, im.height * 2))
        return _clean(pytesseract.image_to_string(im, config="--psm 6"))
    except Exception as e:                       # noqa: BLE001
        print(f"ocr failed for {path}: {e}", file=sys.stderr)
        return None


def pdf_pages(path, out_dir, stem, dpi=110):
    """Yield (page_no, text, png_path) for each page; text from the PDF layer, OCR if empty."""
    if not fitz:
        return []
    out = []
    doc = fitz.open(path)
    for i, page in enumerate(doc, 1):
        text = _clean(page.get_text())
        png = Path(out_dir) / f"{stem}-p{i}.png"
        if not png.exists():
            page.get_pixmap(dpi=dpi).save(png)
        if len(text) < 20 and pytesseract:      # scanned page
            text = ocr_image(png) or text
        out.append((i, text, png))
    return out
