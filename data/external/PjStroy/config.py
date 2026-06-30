"""
Центральная конфигурация пайплайна.
Меняй значения здесь, не трогая остальной код.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"        # скачанные / исходные PDF и HTML
TEXT_DIR = DATA_DIR / "text"      # кэш извлечённого текста (.txt)
CHROMA_DIR = DATA_DIR / "chroma"  # постоянное векторное хранилище

SOURCES_CSV = BASE_DIR / "sources.csv"

# --- LM Studio (OpenAI-совместимый API) ---
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
# ВАЖНО: подставь точный id embedding-модели, как он показан в LM Studio
# (вкладка Developer -> Loaded models). Часто это что-то вроде "text-embedding-bge-m3".
EMBEDDING_MODEL = "text-embedding-bge-m3"
# Модель для ответа в режиме --ask (любая загруженная chat-модель):
CHAT_MODEL = "qwen3-8b"
EMBED_BATCH = 16
REQUEST_TIMEOUT = 180

# --- Чанкинг ---
CHUNK_TARGET_CHARS = 1200    # целевой размер чанка
CHUNK_OVERLAP_CHARS = 150    # перекрытие (для длинных пунктов в fallback-режиме)
MIN_CHUNK_CHARS = 80         # короче этого — выбрасываем (мусор)

# --- OCR (Tesseract) ---
# Языковые пакеты: rus, uzb (латиница), uzb_cyrl (кириллица).
# Установка на macOS: brew install tesseract tesseract-lang
OCR_LANGS = "rus+uzb+uzb_cyrl"
OCR_DPI = 300
TEXT_LAYER_MIN_CHARS = 60     # если на странице текста меньше — считаем её сканом и OCR-им

# --- Сеть (вежливость к серверам) ---
DOWNLOAD_DELAY_SEC = 1.5
USER_AGENT = "NormBaseBot/1.0 (local RAG; contact: you@example.com)"

COLLECTION_NAME = "uz_construction_norms"

# Какие статусы показывать в поиске по умолчанию (исключаем отменённые):
DEFAULT_EXCLUDE_STATUS = "superseded"


def ensure_dirs() -> None:
    for d in (DATA_DIR, RAW_DIR, TEXT_DIR, CHROMA_DIR):
        d.mkdir(parents=True, exist_ok=True)
