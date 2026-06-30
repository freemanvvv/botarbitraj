"""Обогатить метаданные чанков из sources.csv"""
import sys, os, csv
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pathlib import Path
from normbase import config as nb_config
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
import chromadb

# Load CSV
with open(nb_config.SOURCES_CSV, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

# Build doc_id -> metadata lookup
csv_meta = {}
for r in rows:
    doc_id = r['id']
    csv_meta[doc_id] = {
        "doc_type": r.get("doc_type", ""),
        "number": r.get("number", ""),
        "year": r.get("year", ""),
        "title": r.get("title", ""),
        "language": r.get("language", "ru"),
        "status": r.get("status", "unknown"),
        "superseded_by": r.get("superseded_by", ""),
        "source_url": r.get("source_url", ""),
    }
print(f"Загружено {len(csv_meta)} записей из CSV", flush=True)

# Connect to ChromaDB
embed_fn = ONNXMiniLM_L6_V2(preferred_providers=["CoreMLExecutionProvider", "CPUExecutionProvider"])
client = chromadb.PersistentClient(path=str(nb_config.CHROMA_DIR))
collection = client.get_collection(nb_config.COLLECTION_NAME, embedding_function=embed_fn)
print(f"Коллекция: {collection.count()} чанков", flush=True)

# Get ALL doc_ids from the collection (batched)
ids = []
metas = []
docs = []
offset = 0
BATCH_GET = 3000

while True:
    batch = collection.get(limit=BATCH_GET, offset=offset, include=["metadatas", "documents"])
    if not batch["ids"]:
        break
    ids.extend(batch["ids"])
    metas.extend(batch["metadatas"])
    docs.extend(batch["documents"])
    offset += len(batch["ids"])
    print(f"  загружено {offset}...", flush=True)

print(f"Загружено {len(ids)} чанков из БД", flush=True)

# Enrich metadata
updated_ids = []
updated_metas = []
updated_docs = []
enriched = 0
skipped = 0

for idx, (cid, meta, doc) in enumerate(zip(ids, metas, docs)):
    doc_id = meta.get("doc_id", meta.get("source", ""))
    if doc_id in csv_meta:
        csv_row = csv_meta[doc_id]
        new_meta = {
            **meta,
            "doc_type": csv_row["doc_type"],
            "number": csv_row["number"],
            "year": csv_row["year"],
            "title": csv_row["title"],
            "language": csv_row["language"],
            "status": csv_row["status"],
            "superseded_by": csv_row["superseded_by"],
            "source_url": csv_row["source_url"],
        }
        updated_ids.append(cid)
        updated_metas.append(new_meta)
        updated_docs.append(doc)
        enriched += 1
    else:
        skipped += 1
    
    if (idx + 1) % 5000 == 0:
        print(f"  обработано {idx+1}/{len(ids)}...", flush=True)

print(f"\nГотово к обновлению: {enriched} чанков, пропущено: {skipped}", flush=True)

# Update in batches (ChromaDB has limits on batch size)
BATCH = 200
for i in range(0, len(updated_ids), BATCH):
    batch_ids = updated_ids[i:i+BATCH]
    batch_metas = updated_metas[i:i+BATCH]
    batch_docs = updated_docs[i:i+BATCH]
    
    try:
        collection.update(ids=batch_ids, metadatas=batch_metas, documents=batch_docs)
        print(f"  ✅ батч {i//BATCH + 1}: {len(batch_ids)} чанков обновлено", flush=True)
    except Exception as e:
        print(f"  ❌ батч {i//BATCH + 1}: {e}", flush=True)

print(f"\n{'='*60}", flush=True)
print(f"✅ Обогащение завершено!", flush=True)

# Verify: sample enriched chunks
results = collection.get(ids=updated_ids[:3], include=["metadatas"])
for m in results["metadatas"]:
    print(f"  doc_id={m['doc_id']}, type={m.get('doc_type')}, num={m.get('number')}, title={m.get('title')[:40]}")
