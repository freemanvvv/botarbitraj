"""Download and index the 62 missing documents"""
import sys, os, csv, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import requests
from pathlib import Path
from normbase import config as nb_config
from normbase import embed as nb_embed
from normbase import store as nb_store
from normbase import extract as nb_extract
from normbase import chunk as nb_chunk

nb_config.ensure_dirs()
collection = nb_store.get_collection()
current_count = nb_store.count(collection)
print(f"Текущий размер базы: {current_count} чанков", flush=True)

# Find docs not yet downloaded
raw_dir = Path(nb_config.RAW_DIR)
downloaded = {f.stem for f in raw_dir.iterdir() if f.suffix in ('.pdf','.html','.bin')}

with open(nb_config.SOURCES_CSV, newline='', encoding='utf-8') as f:
    rows = list(csv.DictReader(f))

missing = [r for r in rows if r.get('source_url','').strip() and r['id'] not in downloaded]
print(f"Найдено пропущенных: {len(missing)}", flush=True)

total_new = 0
ok = 0
errors = 0

for i, row in enumerate(missing, 1):
    url = row['source_url'].strip()
    doc_id = row['id']
    label = f"{row.get('doc_type','')} {row.get('number','')} — {row.get('title','')[:40]}"
    
    print(f"\n[{i}/{len(missing)}] {doc_id} — {label}", flush=True)
    
    # Try downloading
    ext = ".pdf" if url.lower().endswith(".pdf") else ".html"
    dest = raw_dir / f"{doc_id}{ext}"
    
    try:
        r = requests.get(url, timeout=180, headers={"User-Agent": nb_config.USER_AGENT}, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as out:
            for block in r.iter_content(chunk_size=8192):
                out.write(block)
        
        if dest.exists() and dest.stat().st_size > 100:
            print(f"  ✅ скачан ({dest.stat().st_size/1024:.0f} KB)", flush=True)
        else:
            print(f"  ⚠️ файл пустой", flush=True)
            errors += 1
            continue
            
    except Exception as e:
        print(f"  ❌ не скачался: {e}", flush=True)
        errors += 1
        time.sleep(5)
        continue
    
    # Process
    try:
        pages = nb_extract.extract_document(dest)
        chunks = nb_chunk.chunk_pages(pages, nb_config.CHUNK_TARGET_CHARS,
                                       nb_config.CHUNK_OVERLAP_CHARS, nb_config.MIN_CHUNK_CHARS)
        
        if not chunks:
            print(f"  ⚠️ нет текста", flush=True)
            errors += 1
            continue
        
        texts = [c.text for c in chunks]
        embeddings = nb_embed.embed_texts(texts)
        ids = [f"{doc_id}::{i}" for i in range(len(chunks))]
        metas = []
        for idx, c in enumerate(chunks):
            metas.append({
                "doc_id": doc_id,
                "doc_type": row.get("doc_type", ""),
                "number": row.get("number", ""),
                "year": row.get("year", ""),
                "title": row.get("title", ""),
                "language": row.get("language", "ru"),
                "status": row.get("status", "unknown"),
                "superseded_by": row.get("superseded_by", ""),
                "source_url": url,
                "page_start": c.page_start,
                "page_end": c.page_end,
                "clauses": ", ".join(c.clauses) if c.clauses else "",
                "chunk_index": idx,
            })
        
        nb_store.upsert_chunks(collection, ids, texts, embeddings, metas)
        print(f"  ✅ +{len(chunks)} чанков", flush=True)
        total_new += len(chunks)
        ok += 1
        
    except Exception as e:
        print(f"  ❌ обработка: {e}", flush=True)
        errors += 1
    
    time.sleep(1.5)  # polite delay

print(f"\n{'='*60}", flush=True)
print(f"✅ ДОКАЧАНО:", flush=True)
print(f"   Успешно: {ok} документов, +{total_new} чанков", flush=True)
print(f"   Ошибок: {errors}", flush=True)
print(f"   Всего в базе: {nb_store.count(collection)} чанков", flush=True)
