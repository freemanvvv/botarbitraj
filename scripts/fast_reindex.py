"""Fast re-index using ONNX embeddings (no LM Studio calls)"""
import sys, os, csv, time, hashlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pathlib import Path
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from normbase import config as nb_config
from normbase import store as nb_store
from normbase import extract as nb_extract
from normbase import chunk as nb_chunk
import chromadb
from chromadb.config import Settings

print("Быстрая переиндексация с ONNX-эмбеддингами...", flush=True)

# Use ONNX embedding (local, fast, no LM Studio)
embed_fn = ONNXMiniLM_L6_V2(preferred_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])

# Create collection with ONNX embedding function
client = chromadb.PersistentClient(path=str(nb_config.CHROMA_DIR), settings=Settings(anonymized_telemetry=False))
try:
    client.delete_collection(nb_config.COLLECTION_NAME)
except:
    pass

collection = client.create_collection(
    name=nb_config.COLLECTION_NAME,
    embedding_function=embed_fn,
    metadata={"hnsw:space": "cosine"},
)

# Find all downloaded files
raw_dir = nb_config.RAW_DIR
pdfs = sorted(raw_dir.glob("*.pdf")) + sorted(raw_dir.glob("*.html"))
print(f"Найдено файлов: {len(pdfs)}", flush=True)

total = 0
ok = 0
errors = 0

for i, fpath in enumerate(pdfs, 1):
    doc_id = fpath.stem
    print(f"[{i}/{len(pdfs)}] {doc_id}", flush=True)

    try:
        pages = nb_extract.extract_document(fpath)
        chunks = nb_chunk.chunk_pages(pages, nb_config.CHUNK_TARGET_CHARS,
                                       nb_config.CHUNK_OVERLAP_CHARS, nb_config.MIN_CHUNK_CHARS)
        if not chunks:
            print(f"  ⚠️ пусто", flush=True)
            errors += 1
            continue

        texts = [c.text for c in chunks]
        ids = [f"{doc_id}::{idx}" for idx in range(len(chunks))]
        metas = [{"source": doc_id, "doc_id": doc_id, "chunk_index": idx} for idx in range(len(chunks))]

        # ChromaDB with embedding_function handles embeddings automatically
        # Just add documents - it computes embeddings internally
        collection.add(ids=ids, documents=texts, metadatas=metas)

        print(f"  ✅ +{len(chunks)} чанков", flush=True)
        total += len(chunks)
        ok += 1

    except Exception as e:
        print(f"  ❌ {e}", flush=True)
        errors += 1

print(f"\n{'='*60}", flush=True)
print(f"✅ ПЕРЕИНДЕКСАЦИЯ ЗАВЕРШЕНА:", flush=True)
print(f"   Обработано: {ok} файлов", flush=True)
print(f"   Добавлено: {total} чанков", flush=True)
print(f"   Ошибок: {errors}", flush=True)
print(f"   Всего в базе: {collection.count()}", flush=True)
