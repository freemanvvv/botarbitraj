"""
Главный пайплайн индексации NormBase (адаптирован для Construction AI Copilot).
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from . import config as nb_config
from . import embed as nb_embed
from . import store as nb_store
from . import chunk as nb_chunk
from . import extract as nb_extract


def read_sources(only: Optional[str], limit: Optional[int]) -> List[Dict]:
    rows: List[Dict] = []
    with open(nb_config.SOURCES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if only and row["id"] != only:
                continue
            rows.append(row)
    if limit:
        rows = rows[:limit]
    return rows


def resolve_file(row: Dict) -> Optional[Path]:
    """Возвращает путь к локальному файлу: из local_path или скачивает source_url."""
    local = (row.get("local_path") or "").strip()
    if local:
        p = Path(local)
        if not p.is_absolute():
            p = nb_config.BASE_DIR / p
        return p if p.exists() else None

    url = (row.get("source_url") or "").strip()
    if not url:
        return None

    ext = ".pdf" if url.lower().endswith(".pdf") else (
        ".html" if url.lower().endswith((".html", ".htm")) else ".bin")
    dest = nb_config.RAW_DIR / f"{row['id']}{ext}"
    if dest.exists():
        return dest

    print(f"    скачиваю {url}")
    try:
        r = requests.get(url, timeout=nb_config.REQUEST_TIMEOUT,
                         headers={"User-Agent": nb_config.USER_AGENT}, stream=True)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if dest.suffix == ".bin":
            if "pdf" in ctype:
                dest = dest.with_suffix(".pdf")
            elif "html" in ctype:
                dest = dest.with_suffix(".html")
        with open(dest, "wb") as out:
            for block in r.iter_content(chunk_size=8192):
                out.write(block)
        time.sleep(nb_config.DOWNLOAD_DELAY_SEC)
        return dest
    except Exception as e:
        print(f"    [error] не удалось скачать: {e}")
        return None


def build_meta(row: Dict, chunk_item, idx: int) -> Dict:
    return {
        "doc_id": row["id"],
        "doc_type": row.get("doc_type", ""),
        "number": row.get("number", ""),
        "year": row.get("year", ""),
        "title": row.get("title", ""),
        "language": row.get("language", ""),
        "status": row.get("status", "unknown"),
        "superseded_by": row.get("superseded_by", ""),
        "source_url": row.get("source_url", ""),
        "page_start": chunk_item.page_start,
        "page_end": chunk_item.page_end,
        "clauses": chunk_item.clauses,
        "chunk_index": idx,
    }


def process_document(collection, row: Dict) -> int:
    path = resolve_file(row)
    if not path:
        print(f"    [skip] нет файла (заполни local_path или source_url)")
        return 0

    pages = nb_extract.extract_document(path)
    ocr_pages = sum(1 for p in pages if p.ocr)
    if ocr_pages:
        print(f"    OCR применён к {ocr_pages}/{len(pages)} стр.")

    chunks = nb_chunk.chunk_pages(pages, nb_config.CHUNK_TARGET_CHARS,
                                   nb_config.CHUNK_OVERLAP_CHARS, nb_config.MIN_CHUNK_CHARS)
    if not chunks:
        print("    [skip] не получилось извлечь содержательный текст")
        return 0

    texts = [c.text for c in chunks]
    embeddings = nb_embed.embed_texts(texts)
    ids = [f"{row['id']}::{i}" for i in range(len(chunks))]
    metas = [build_meta(row, c, i) for i, c in enumerate(chunks)]

    nb_store.upsert_chunks(collection, ids, texts, embeddings, metas)
    return len(chunks)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="обработать только документ с этим id")
    ap.add_argument("--limit", type=int, help="ограничить число документов")
    ap.add_argument("--reset", action="store_true", help="очистить коллекцию перед загрузкой")
    args = ap.parse_args()

    nb_config.ensure_dirs()
    if not nb_embed.check_connection():
        print("Запусти сервер в LM Studio и загрузи embedding-модель, затем повтори.")
        return

    if args.reset:
        client = __import__("chromadb").PersistentClient(path=str(nb_config.CHROMA_DIR))
        try:
            client.delete_collection(nb_config.COLLECTION_NAME)
            print("Коллекция очищена.")
        except Exception:
            pass

    collection = nb_store.get_collection()
    rows = read_sources(args.only, args.limit)
    print(f"К обработке: {len(rows)} документ(ов)\n")

    total = 0
    for row in rows:
        label = f"{row.get('doc_type','')} {row.get('number','')} — {row.get('title','')}".strip()
        print(f"[{row['id']}] {label}")
        n = process_document(collection, row)
        if n:
            print(f"    +{n} чанков")
        total += n
        print()

    print(f"Готово. Добавлено чанков: {total}. Всего в базе: {nb_store.count(collection)}")


if __name__ == "__main__":
    main()
