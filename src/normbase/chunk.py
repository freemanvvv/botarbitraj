"""
Чанкинг с учётом структуры нормативов.

Нормы пронумерованы пунктами (1.1, 2.3.4 и т.п.). Мы режем текст по этим
пунктам и пакуем соседние пункты в чанки до целевого размера. Это даёт
чанки, которые удобно цитировать: «КМК 2.02.01-98, п. 3.2, стр. 41».

Если разметки пунктов нет (сплошной текст / плохой OCR) — fallback на
скользящее окно с перекрытием.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# строка вида "1.", "1.1", "2.3.4", "10.2.1." в начале строки
CLAUSE_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3}){0,3})\.?(?:\s|$)")


@dataclass
class Unit:
    clause: Optional[str]
    text: str
    page: int


@dataclass
class Chunk:
    text: str
    page_start: int
    page_end: int
    clauses: List[str] = field(default_factory=list)


def _segment(pages) -> List[Unit]:
    """Разбиваем страницы на пункты."""
    units: List[Unit] = []
    current: Optional[Unit] = None
    for p in pages:
        for raw_line in p.text.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue
            m = CLAUSE_RE.match(line)
            if m:
                if current is not None:
                    units.append(current)
                current = Unit(clause=m.group(1), text=line, page=p.page)
            else:
                if current is None:
                    current = Unit(clause=None, text=line, page=p.page)
                else:
                    current.text += "\n" + line
    if current is not None:
        units.append(current)
    return units


def _sliding(text: str, page: int, target: int, overlap: int, min_chars: int,
             clause: Optional[str]) -> List[Chunk]:
    """Скользящее окно для очень длинного пункта или бесструктурного текста."""
    chunks: List[Chunk] = []
    step = max(1, target - overlap)
    for start in range(0, len(text), step):
        piece = text[start:start + target].strip()
        if len(piece) >= min_chars:
            chunks.append(Chunk(text=piece, page_start=page, page_end=page,
                                clauses=[clause] if clause else []))
        if start + target >= len(text):
            break
    return chunks


def chunk_pages(pages, target: int, overlap: int, min_chars: int) -> List[Chunk]:
    units = _segment(pages)
    if not units:
        return []

    # есть ли вообще нумерация пунктов?
    labelled = sum(1 for u in units if u.clause)
    if labelled < max(2, len(units) // 20):
        # практически нет структуры — режем весь текст окном
        full = "\n".join(u.text for u in units)
        page = units[0].page
        return _sliding(full, page, target, overlap, min_chars, None)

    chunks: List[Chunk] = []
    buf: List[Unit] = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if not buf:
            return
        text = "\n".join(u.text for u in buf).strip()
        if len(text) >= min_chars:
            cl = [u.clause for u in buf if u.clause]
            chunks.append(Chunk(
                text=text,
                page_start=min(u.page for u in buf),
                page_end=max(u.page for u in buf),
                clauses=cl,
            ))
        buf = []
        buf_len = 0

    for u in units:
        if len(u.text) > target:
            # один пункт длиннее целевого размера — сбрасываем буфер и режем его окном
            flush()
            chunks.extend(_sliding(u.text, u.page, target, overlap, min_chars, u.clause))
            continue
        if buf_len + len(u.text) > target and buf:
            flush()
        buf.append(u)
        buf_len += len(u.text) + 1
    flush()
    return chunks
