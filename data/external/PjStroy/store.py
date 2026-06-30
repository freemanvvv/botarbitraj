"""
Обёртка над ChromaDB (постоянное локальное хранилище, без отдельного контейнера).
Эмбеддинги считаем сами (через LM Studio) и кладём напрямую — поэтому
встроенная embedding-функция Chroma не используется.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import chromadb

import config


def get_collection():
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    return client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def _sanitize(meta: Dict) -> Dict:
    """Chroma принимает только str/int/float/bool. None -> '', списки -> строка."""
    clean = {}
    for k, v in meta.items():
        if v is None:
            clean[k] = ""
        elif isinstance(v, (str, int, float, bool)):
            clean[k] = v
        elif isinstance(v, (list, tuple)):
            clean[k] = ", ".join(str(x) for x in v)
        else:
            clean[k] = str(v)
    return clean


def upsert_chunks(collection, ids: List[str], documents: List[str],
                  embeddings: List[List[float]], metadatas: List[Dict]) -> None:
    collection.upsert(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=[_sanitize(m) for m in metadatas],
    )


def query(collection, query_embedding: List[float], n_results: int = 5,
          where: Optional[Dict] = None) -> List[Dict]:
    res = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        where=where,
    )
    out: List[Dict] = []
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    dists = res.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, dists):
        out.append({"text": doc, "meta": meta, "distance": dist})
    return out


def count(collection) -> int:
    return collection.count()
