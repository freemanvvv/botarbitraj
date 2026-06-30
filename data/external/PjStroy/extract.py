"""
Извлечение текста из исходных файлов.
PDF: сначала пробуем текстовый слой (PyMuPDF). Если страница — скан
(текста почти нет), рендерим её в картинку и прогоняем через Tesseract OCR.
HTML: вытаскиваем видимый текст через BeautifulSoup.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import List

import config


@dataclass
class PageText:
    page: int          # номер страницы (1-based); для HTML всегда 1
    text: str
    ocr: bool          # был ли применён OCR


def _ocr_page(page, langs: str, dpi: int) -> str:
    """Рендер страницы PDF в картинку и распознавание текста."""
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang=langs)


def extract_pdf(path: Path) -> List[PageText]:
    import fitz  # PyMuPDF

    pages: List[PageText] = []
    doc = fitz.open(path)
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            if len(text.strip()) < config.TEXT_LAYER_MIN_CHARS:
                # вероятно скан — пробуем OCR
                try:
                    ocr_text = _ocr_page(page, config.OCR_LANGS, config.OCR_DPI)
                except Exception as e:  # OCR недоступен/ошибка — не валим весь файл
                    print(f"    [warn] OCR не сработал на стр. {i}: {e}")
                    ocr_text = text
                pages.append(PageText(page=i, text=ocr_text, ocr=True))
            else:
                pages.append(PageText(page=i, text=text, ocr=False))
    finally:
        doc.close()
    return pages


def extract_html(path: Path) -> List[PageText]:
    from bs4 import BeautifulSoup

    html = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    text = soup.get_text("\n")
    # схлопываем пустые строки
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return [PageText(page=1, text=text, ocr=False)]


def extract_document(path: Path) -> List[PageText]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix in (".html", ".htm"):
        return extract_html(path)
    # как текст
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [PageText(page=1, text=text, ocr=False)]
