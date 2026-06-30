"""Добить оставшиеся ~2k чанков через прямое SQLite-обновление"""
import sys, os, csv, sqlite3, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from pathlib import Path
from normbase import config as nb_config

# Load CSV
with open(nb_config.SOURCES_CSV, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

csv_meta = {}
for r in rows:
    csv_meta[r['id']] = r

print(f"CSV: {len(csv_meta)} docs", flush=True)

conn = sqlite3.connect(str(nb_config.CHROMA_DIR / "chroma.sqlite3"))
cursor = conn.cursor()

# Find chunks WITHOUT title metadata
cursor.execute('''
    SELECT DISTINCT e.id, e.embedding_id FROM embeddings e
    WHERE e.id NOT IN (
        SELECT DISTINCT e2.id FROM embeddings e2
        JOIN embedding_metadata em2 ON e2.id = em2.id
        WHERE em2.key = 'title' AND em2.string_value IS NOT NULL AND em2.string_value != ''
    )
    LIMIT 5000
''')
rows_to_update = cursor.fetchall()
print(f"Нужно обновить: {len(rows_to_update)} чанков", flush=True)

updated = 0
errors = 0

for idx, (row_id, embedding_id) in enumerate(rows_to_update):
    # Get existing metadata for this chunk
    cursor.execute('''
        SELECT key, string_value, int_value, float_value, bool_value 
        FROM embedding_metadata 
        WHERE id = ?
    ''', (row_id,))
    existing = {r[0]: r[1] or r[2] or r[3] or r[4] for r in cursor.fetchall()}
    
    # Find doc_id
    doc_id = existing.get('doc_id') or existing.get('source', '')
    if doc_id not in csv_meta:
        continue
    
    csv_row = csv_meta[doc_id]
    new_fields = {
        "doc_type": csv_row.get("doc_type", ""),
        "number": csv_row.get("number", ""),
        "year": csv_row.get("year", ""),
        "title": csv_row.get("title", ""),
        "language": csv_row.get("language", "ru"),
        "status": csv_row.get("status", "unknown"),
        "superseded_by": csv_row.get("superseded_by", ""),
        "source_url": csv_row.get("source_url", ""),
    }
    
    for key, val in new_fields.items():
        if key not in existing or not existing[key]:
            try:
                cursor.execute(
                    "INSERT OR IGNORE INTO embedding_metadata (id, key, string_value) VALUES (?, ?, ?)",
                    (row_id, key, str(val) if val else "")
                )
            except Exception:
                pass
    
    updated += 1
    if (idx + 1) % 200 == 0:
        conn.commit()
        print(f"  {idx+1}/{len(rows_to_update)}", flush=True)

conn.commit()
conn.close()
print(f"\n✅ Обновлено: {updated} чанков", flush=True)
