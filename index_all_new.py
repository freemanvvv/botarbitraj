"""Index ALL docs from sources.csv with NormBase"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

print("NormBase: полная индексация ВСЕХ новых документов...", flush=True)

from normbase import ingest as nb_ingest
from normbase import store as nb_store
from normbase import config as nb_config
import chromadb

nb_config.ensure_dirs()
client = chromadb.PersistentClient(path=str(nb_config.CHROMA_DIR))

# Clear old collection and start fresh
try:
    client.delete_collection(nb_config.COLLECTION_NAME)
    print("Коллекция очищена.", flush=True)
except:
    pass

collection = nb_store.get_collection()
rows = nb_ingest.read_sources(only=None, limit=None)
print(f"Всего документов: {len(rows)}", flush=True)

total = 0
ok = 0
skipped = 0
errors = 0

for i, row in enumerate(rows, 1):
    url = (row.get("source_url") or "").strip()
    if not url:
        skipped += 1
        continue

    label = f"{row.get('doc_type','')} {row.get('number','')}" 
    print(f"[{i}/{len(rows)}] {row['id']} — {label}", flush=True)
    
    try:
        n = nb_ingest.process_document(collection, row)
        if n:
            print(f"  ✅ {n} чанков", flush=True)
            total += n
            ok += 1
        else:
            print(f"  ⚠️ пусто", flush=True)
            skipped += 1
    except Exception as e:
        print(f"  ❌ {e}", flush=True)
        errors += 1

print(f"\n{'='*60}", flush=True)
print(f"✅ ИТОГ: обработано {ok} док, добавлено {total} чанков", flush=True)
print(f"   пропущено (нет URL): {skipped}", flush=True)
print(f"   ошибок: {errors}", flush=True)
print(f"   всего в базе: {nb_store.count(collection)}", flush=True)
