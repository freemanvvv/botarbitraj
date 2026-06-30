"""
Клиент эмбеддингов через LM Studio (эндпоинт /embeddings, OpenAI-совместимый).
В LM Studio должна быть загружена embedding-модель (рекомендуется bge-m3)
и включён локальный сервер.
"""
from __future__ import annotations

from typing import List

import requests

from . import config


def _post_embeddings(inputs: List[str]) -> List[List[float]]:
    url = f"{config.LMSTUDIO_BASE_URL}/embeddings"
    payload = {"model": config.EMBEDDING_MODEL, "input": inputs}
    resp = requests.post(url, json=payload, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()["data"]
    # сортируем по index на всякий случай
    data = sorted(data, key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Батчевое получение эмбеддингов с сохранением порядка."""
    out: List[List[float]] = []
    for i in range(0, len(texts), config.EMBED_BATCH):
        batch = texts[i:i + config.EMBED_BATCH]
        out.extend(_post_embeddings(batch))
    return out


def embed_query(text: str) -> List[float]:
    return _post_embeddings([text])[0]


def check_connection() -> bool:
    """Быстрая проверка, что сервер LM Studio отвечает."""
    try:
        r = requests.get(f"{config.LMSTUDIO_BASE_URL}/models",
                         timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"[error] LM Studio недоступен на {config.LMSTUDIO_BASE_URL}: {e}")
        return False
