"""
Конфигурация Construction AI Copilot
"""
import os

# LM Studio API
LM_STUDIO_BASE_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")

# Доступные модели
MODELS = {
    "qwen3-14b": {
        "id": "qwen/qwen3-14b",
        "description": "Сметы, инженерный текст, агентные задачи",
        "temp": 0.3,
        "top_p": 0.9,
        "max_context": 16384,
    },
    "qwen3-8b": {
        "id": "qwen2.5-coder-7b-instruct",  # fallback — 7B coder
        "description": "Быстрые ответы, документация",
        "temp": 0.3,
        "top_p": 0.9,
        "max_context": 16384,
    },
    "llama-uz": {
        "id": "mradermacher/Llama-3.1-8B-Instuct-Uz-GGUF",
        "description": "Узбекский язык, КМК, диалог с заказчиком",
        "temp": 0.4,
        "top_p": 0.95,
        "max_context": 16384,
    },
}

# Embedding model
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-nomic-embed-text-v1.5")
EMBEDDING_DIM = 768

# Хранилища
CHROMA_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "chroma_db")
NORMATIVES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "normatives")
PRICING_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "pricing")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output")

os.makedirs(CHROMA_DB_PATH, exist_ok=True)
os.makedirs(NORMATIVES_DIR, exist_ok=True)
os.makedirs(PRICING_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
