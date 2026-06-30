"""
RAG-пайплайн — объединённая версия.
Поддерживает два режима:
- simple: наша исходная реализация (ChromaDB + ONNX-эмбеддинги)
- normbase: продвинутая реализация NormBase (OCR, чанкинг по пунктам, статусы)
"""
import os
import hashlib
from datetime import datetime
from typing import Optional

from .config import CHROMA_DB_PATH, NORMATIVES_DIR

# ========== SIMPLE MODE (ChromaDB + ONNX) ==========

def get_embedding_fn():
    """Лёгкая ONNX embedding-функция ChromaDB."""
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    return ONNXMiniLM_L6_V2()


def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200):
    """Разбивает текст на чанки."""
    words = text.split()
    chunks = []
    start = 0
    iteration = 0

    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk = " ".join(words[start:end])
        chunks.append((chunk, len(chunks)))
        iteration += 1
        if iteration > 1000 or end >= len(words):
            break
        start = max(start + 1, end - overlap)

    return chunks


class SimpleRAG:
    """Простой RAG на ChromaDB + ONNX эмбеддинги (без OCR, без статусов)."""

    def __init__(self, collection_name: str = "normatives"):
        import chromadb
        from chromadb.config import Settings

        self.collection_name = collection_name
        self.embed_fn = get_embedding_fn()
        self.client = chromadb.PersistentClient(
            path=CHROMA_DB_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"},
        )

    def index_file(self, filepath: str) -> int:
        if not os.path.isfile(filepath):
            raise FileNotFoundError(f"Файл не найден: {filepath}")

        filename = os.path.basename(filepath)
        ext = os.path.splitext(filepath)[1].lower()

        if ext == ".pdf":
            import pdfplumber
            text = ""
            with pdfplumber.open(filepath) as pdf:
                for page in pdf.pages:
                    pt = page.extract_text()
                    if pt:
                        text += pt + "\n"
        elif ext in (".txt", ".md"):
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            return 0

        if not text.strip():
            return 0

        chunks = chunk_text(text)
        ids, documents, metadatas = [], [], []

        for i, (chunk, _) in enumerate(chunks):
            chunk_hash = hashlib.md5(f"{filename}:{i}".encode()).hexdigest()
            ids.append(chunk_hash)
            documents.append(chunk)
            metadatas.append({"source": filename, "chunk": i, "filepath": filepath})

        self.collection.add(ids=ids, documents=documents, metadatas=metadatas)
        return len(chunks)

    def index_directory(self, directory: str | None = None) -> dict:
        dir_path = directory or NORMATIVES_DIR
        if not os.path.isdir(dir_path):
            raise FileNotFoundError(f"Директория не найдена: {dir_path}")

        results = {}
        for filename in sorted(os.listdir(dir_path)):
            filepath = os.path.join(dir_path, filename)
            if not os.path.isfile(filepath):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".pdf", ".txt", ".md"):
                continue
            try:
                count = self.index_file(filepath)
                if count > 0:
                    results[filename] = count
                    print(f"  ✅ {filename}: {count} чанков")
            except Exception as e:
                print(f"  ❌ {filename}: {e}")

        return results

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        if self.collection.count() == 0:
            return []
        results = self.collection.query(query_texts=[query], n_results=min(top_k, self.collection.count()))
        output = []
        for i in range(len(results["ids"][0])):
            output.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "score": 1 - (results["distances"][0][i] if results.get("distances") else [0])[0] if results.get("distances") else 0,
                "source": results["metadatas"][0][i].get("source", "unknown"),
            })
        return output

    def count(self) -> int:
        return self.collection.count()

    def clear(self):
        self.client.delete_collection(self.collection_name)
        self.collection = self.client.create_collection(
            name=self.collection_name,
            embedding_function=self.embed_fn,
            metadata={"hnsw:space": "cosine"},
        )


# ========== NORMBASE MODE (OCR + structured chunks + statuses) ==========

class NormbaseRAG:
    """RAG с использованием NormBase (PyMuPDF, Tesseract OCR, чанкинг по пунктам)."""

    def __init__(self, collection_name: str = "uz_construction_norms"):
        self.collection_name = collection_name
        # Импортируем NormBase модули лениво
        from .normbase import store as nb_store
        from .normbase import embed as nb_embed
        from .normbase import config as nb_config

        self.nb_store = nb_store
        self.nb_embed = nb_embed
        self.nb_config = nb_config

        nb_config.COLLECTION_NAME = collection_name
        nb_config.ensure_dirs()
        self.collection = nb_store.get_collection()

    def index_from_csv(self, csv_path: str | None = None, limit: int | None = None, only: str | None = None) -> int:
        """Индексирует документы из sources.csv через NormBase."""
        from .normbase import ingest

        # Перенаправляем stdout
        total = 0
        rows = ingest.read_sources(only, limit)
        print(f"📋 Документов к обработке: {len(rows)}")

        for row in rows:
            label = f"{row.get('doc_type','')} {row.get('number','')} — {row.get('title','')}".strip()
            print(f"\n[{row['id']}] {label}")
            n = ingest.process_document(self.collection, row)
            if n:
                print(f"    +{n} чанков")
                total += n
            else:
                print(f"    ⚠️  не обработан (нет файла или ошибка)")

        print(f"\n✅ Добавлено чанков: {total}. Всего в базе: {self.nb_store.count(self.collection)}")
        return total

    def index_file(self, filepath: str, doc_id: str | None = None,
                   doc_type: str = "Документ", title: str = "") -> int:
        """Индексирует один файл через NormBase pipeline."""
        path = os.path.abspath(filepath)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Файл не найден: {path}")

        from .normbase import extract, chunk
        from .normbase import config as nb_config

        pages = extract.extract_document(path)
        chunks = chunk.chunk_pages(pages, nb_config.CHUNK_TARGET_CHARS,
                                    nb_config.CHUNK_OVERLAP_CHARS, nb_config.MIN_CHUNK_CHARS)
        if not chunks:
            return 0

        texts = [c.text for c in chunks]
        embeddings = self.nb_embed.embed_texts(texts)

        doc_id = doc_id or os.path.splitext(os.path.basename(path))[0]
        ids = [f"{doc_id}::{i}" for i in range(len(chunks))]
        metas = []
        for i, c in enumerate(chunks):
            metas.append({
                "doc_id": doc_id,
                "doc_type": doc_type,
                "title": title or os.path.basename(path),
                "language": "ru",
                "status": "active",
                "page_start": c.page_start,
                "page_end": c.page_end,
                "clauses": ", ".join(c.clauses) if c.clauses else "",
                "chunk_index": i,
                "source": os.path.basename(path),
            })

        self.nb_store.upsert_chunks(self.collection, ids, texts, embeddings, metas)
        return len(chunks)

    def index_directory(self, directory: str | None = None) -> dict:
        """Индексирует все PDF из директории через NormBase."""
        dir_path = directory or NORMATIVES_DIR
        if not os.path.isdir(dir_path):
            raise FileNotFoundError(f"Директория не найдена: {dir_path}")

        results = {}
        for filename in sorted(os.listdir(dir_path)):
            filepath = os.path.join(dir_path, filename)
            if not os.path.isfile(filepath):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".pdf", ".txt", ".md"):
                continue
            try:
                count = self.index_file(filepath)
                if count > 0:
                    results[filename] = count
                    print(f"  ✅ {filename}: {count} чанков")
            except Exception as e:
                print(f"  ❌ {filename}: {e}")

        return results

    def search(self, query: str, top_k: int = 5, status_mode: str = "default") -> list[dict]:
        """Поиск с поддержкой фильтрации по статусу."""
        from .normbase.query import retrieve, format_citation

        q_emb = self.nb_embed.embed_query(query)
        where = None if status_mode == "all" else {"status": "active"} if status_mode == "active" else {"status": {"$ne": self.nb_config.DEFAULT_EXCLUDE_STATUS}}

        results = self.nb_store.query(self.collection, q_emb, n_results=top_k, where=where)
        output = []
        for r in results:
            score = 1 - r["distance"] if r.get("distance") is not None else 0
            citation = format_citation(r["meta"])
            output.append({
                "text": r["text"],
                "score": score,
                "citation": citation,
                "meta": r["meta"],
            })
        return output

    def count(self) -> int:
        return self.nb_store.count(self.collection)

    def clear(self):
        import chromadb
        client = chromadb.PersistentClient(path=str(self.nb_config.CHROMA_DIR))
        try:
            client.delete_collection(self.collection_name)
            print("Коллекция очищена.")
        except Exception:
            pass
        self.collection = self.nb_store.get_collection()


# ========== Фабрика ==========

def get_rag(mode: str = "simple", collection_name: str | None = None) -> SimpleRAG | NormbaseRAG:
    """Возвращает RAG-режим."""
    if mode == "normbase":
        return NormbaseRAG(collection_name or "uz_construction_norms")
    return SimpleRAG(collection_name or "normatives")
