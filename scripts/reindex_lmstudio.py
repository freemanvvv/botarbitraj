"""
Полная переиндексация через LM Studio (nomic-embed-text-v1.5, 768d).
Удаляет старую коллекцию, создаёт новую без embedding_function,
считает эмбеддинги через LM Studio API и кладёт векторы напрямую.
"""
import sys, os, csv, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pathlib import Path
from normbase import config as nb_config
from normbase import embed as nb_embed
from normbase import store as nb_store
from normbase import extract as nb_extract
from normbase import chunk as nb_chunk
import chromadb

CHROMA_DIR = nb_config.CHROMA_DIR
COLLECTION_NAME = nb_config.COLLECTION_NAME
RAW_DIR = nb_config.RAW_DIR

print(f"🚀 Переиндексация: {COLLECTION_NAME} через LM Studio ({nb_config.EMBEDDING_MODEL})", flush=True)
print(f"   Размерность: 768, батч: {nb_config.EMBED_BATCH}", flush=True)

# Проверим LM Studio
if not nb_embed.check_connection():
    print("❌ LM Studio не отвечает. Запусти сервер и загрузи модель эмбеддингов.", flush=True)
    sys.exit(1)

# Удаляем старую коллекцию
client = chromadb.PersistentClient(path=str(CHROMA_DIR))
try:
    client.delete_collection(COLLECTION_NAME)
    print(f"🗑️ Старая коллекция удалена", flush=True)
except:
    print(f"ℹ️ Коллекции не было, создаём новую", flush=True)

# Создаём новую коллекцию БЕЗ embedding_function (кладём векторы сами)
collection = client.create_collection(
    name=COLLECTION_NAME,
    metadata={"hnsw:space": "cosine"},
)

# Загружаем CSV для метаданных
with open(nb_config.SOURCES_CSV, newline='', encoding='utf-8') as f:
    csv_rows = list(csv.DictReader(f))
csv_meta = {r['id']: r for r in csv_rows}
print(f"📋 CSV: {len(csv_meta)} записей", flush=True)

# Находим все файлы
pdfs = sorted(RAW_DIR.glob("*.pdf")) + sorted(RAW_DIR.glob("*.html"))
print(f"📄 Найдено файлов: {len(pdfs)}", flush=True)

total_chunks = 0
ok_files = 0
error_files = 0

for i, fpath in enumerate(pdfs, 1):
    doc_id = fpath.stem
    label = f"[{i}/{len(pdfs)}] {doc_id}"
    csv_row = csv_meta.get(doc_id, {})
    title = csv_row.get('title', '')[:50]
    nb = csv_row.get('number', '')
    dt = csv_row.get('doc_type', '')
    
    print(f"\n{label} {dt} {nb} — {title}", flush=True)
    
    try:
        pages = nb_extract.extract_document(fpath)
        chunks = nb_chunk.chunk_pages(pages, nb_config.CHUNK_TARGET_CHARS,
                                       nb_config.CHUNK_OVERLAP_CHARS, nb_config.MIN_CHUNK_CHARS)
        
        if not chunks:
            print(f"  ⚠️ пусто", flush=True)
            error_files += 1
            continue
        
        texts = [c.text for c in chunks]
        
        # Эмбеддинги через LM Studio
        t0 = time.time()
        embeddings = nb_embed.embed_texts(texts)
        t1 = time.time()
        
        ids = [f"{doc_id}::{idx}" for idx in range(len(chunks))]
        
        # Богатые метаданные
        metas = []
        for idx, c in enumerate(chunks):
            metas.append({
                "doc_id": doc_id,
                "doc_type": csv_row.get("doc_type", ""),
                "number": csv_row.get("number", ""),
                "year": csv_row.get("year", ""),
                "title": csv_row.get("title", ""),
                "language": csv_row.get("language", "ru"),
                "status": csv_row.get("status", "unknown"),
                "superseded_by": csv_row.get("superseded_by", ""),
                "source_url": csv_row.get("source_url", ""),
                "source": doc_id,
                "chunk_index": idx,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "clauses": ", ".join(c.clauses) if c.clauses else "",
            })
        
        # Сохраняем с явными эмбеддингами
        nb_store.upsert_chunks(collection, ids, texts, embeddings, metas)
        
        print(f"  ✅ +{len(chunks)} чанков ({t1-t0:.1f}с)", flush=True)
        total_chunks += len(chunks)
        ok_files += 1
        
    except Exception as e:
        print(f"  ❌ {e}", flush=True)
        error_files += 1
    
    # Задержка между документами (вежливость к LM Studio)
    time.sleep(0.5)

print(f"\n{'='*60}", flush=True)
print(f"✅ ПЕРЕИНДЕКСАЦИЯ ЗАВЕРШЕНА:", flush=True)
print(f"   Успешно: {ok_files} файлов, {total_chunks} чанков", flush=True)
print(f"   Ошибок: {error_files}", flush=True)
print(f"   Всего в базе: {collection.count()}", flush=True)
