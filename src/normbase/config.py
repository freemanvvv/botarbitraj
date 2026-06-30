"""
Конфигурация NormBase (адаптирована для Construction AI Copilot).
"""
from pathlib import Path

# Корень проекта
BASE_DIR = Path(__file__).resolve().parent.parent  # src/
PROJECT_DIR = BASE_DIR.parent  # construction-ai-copilot/
DATA_DIR = PROJECT_DIR / "data"
NORMATIVES_DIR = DATA_DIR / "normatives"   # исходные PDF/TXT/MD
CACHE_DIR = DATA_DIR / "normbase_cache"     # кэш извлечённого текста
RAW_DIR = DATA_DIR / "normbase_raw"          # скачанные PDF/HTML
TEXT_DIR = DATA_DIR / "normbase_text"        # кэш текста
CHROMA_DIR = DATA_DIR / "chroma_db"         # векторное хранилище

SOURCES_CSV = BASE_DIR / "normbase" / "sources.csv"

# --- LM Studio (OpenAI-совместимый API) ---
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
EMBEDDING_MODEL = "text-embedding-nomic-embed-text-v1.5"
CHAT_MODEL = "qwen/qwen3-14b"
EMBED_BATCH = 16
REQUEST_TIMEOUT = 180

# --- Чанкинг ---
CHUNK_TARGET_CHARS = 600
CHUNK_OVERLAP_CHARS = 80
MIN_CHUNK_CHARS = 60

# --- OCR (Tesseract) ---
OCR_LANGS = "rus+uzb+uzb_cyrl"
OCR_DPI = 300
TEXT_LAYER_MIN_CHARS = 60

# --- Сеть ---
DOWNLOAD_DELAY_SEC = 1.5
USER_AGENT = "ConstructionAICopilot/1.0"

COLLECTION_NAME = "uz_construction_norms"
DEFAULT_EXCLUDE_STATUS = "superseded"


def ensure_dirs() -> None:
    for d in (DATA_DIR, NORMATIVES_DIR, RAW_DIR, TEXT_DIR, CHROMA_DIR):
        d.mkdir(parents=True, exist_ok=True)
