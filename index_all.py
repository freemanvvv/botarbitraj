"""Full NormBase index script"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

print("NormBase: полная индексация...", flush=True)

from normbase import ingest as nb_ingest
from normbase import store as nb_store
from normbase import config as nb_config

nb_config.ensure_dirs()

# Clear previous
client = __import__("chromadb").PersistentClient(path=str(nb_config.CHROMA_DIR))
try:
    client.delete_collection(nb_config.COLLECTION_NAME)
    print("Коллекция очищена.", flush=True)
except Exception:
    pass

collection = nb_store.get_collection()

# Process all docs with URLs
rows = nb_ingest.read_sources(only=None, limit=None)
print(f"Документов: {len(rows)}", flush=True)

total = 0
for row in rows:
    url = (row.get("source_url") or "").strip()
    if not url:
        print(f"[{row['id']}] ⚠️ нет URL, пропускаю", flush=True)
        continue

    label = f"{row.get('doc_type','')} {row.get('number','')} — {row.get('title','')[:50]}"
    print(f"\n[{row['id']}] {label}", flush=True)
    n = nb_ingest.process_document(collection, row)
    if n:
        print(f"  ✅ {n} чанков", flush=True)
        total += n
    else:
        print(f"  ⚠️ пусто", flush=True)

print(f"\n{'='*50}", flush=True)
print(f"✅ Готово! Добавлено {total} чанков. Всего в базе: {nb_store.count(collection)}", flush=True)
print(f"{'='*50}", flush=True)
