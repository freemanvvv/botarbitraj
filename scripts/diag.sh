#!/bin/bash
# Диагностика окружения для 3D-пайплайна (gsplat) и веб-приложения.
# Запуск на Mac:  bash scripts/diag.sh
# Собирает всё в один вывод — скопируй его целиком в чат.
# Ничего не устанавливает и не меняет, только читает.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT" || exit 1

line() { printf '\n===== %s =====\n' "$1"; }
have() { command -v "$1" >/dev/null 2>&1 && echo "✅ $1 → $(command -v "$1")" || echo "❌ $1 не найден в PATH"; }

line "СИСТЕМА"
uname -a
sw_vers 2>/dev/null
echo "arch: $(uname -m)"

line "GIT"
git rev-parse --abbrev-ref HEAD 2>/dev/null
git log --oneline -1 2>/dev/null

line "PYTHON / VENV"
echo "which python3: $(command -v python3)"
python3 --version 2>&1
if [ -d ".venv" ]; then
  echo "venv: есть (.venv)"
  ./.venv/bin/python -c "import uvicorn, fastapi; print('uvicorn+fastapi OK', uvicorn.__version__)" 2>&1
else
  echo "venv: НЕТ (.venv не найдена)"
fi

line "NODE / FRONTEND"
have node
have npx
[ -f webapp/frontend/dist/index.html ] && echo "✅ dist собран" || echo "❌ dist НЕ собран (нужен npx vite build)"

line "FFMPEG"
have ffmpeg
command -v ffmpeg >/dev/null 2>&1 && ffmpeg -version 2>/dev/null | head -1

line "COLMAP"
have colmap
if command -v colmap >/dev/null 2>&1; then
  echo "--- поддержка GPU-SIFT (для feature_extractor) ---"
  colmap feature_extractor --help 2>&1 | grep -i "use_gpu" || echo "(опции use_gpu нет → сборка без GPU-SIFT, это ок, пайплайн её больше не передаёт)"
fi

line "BRUSH (трейнер 3DGS на Mac, headless-бинарь brush-cli)"
BB="${BRUSH_BIN:-$(command -v brush-cli 2>/dev/null || command -v brush 2>/dev/null)}"
if [ -n "$BB" ] && [ -x "$BB" ]; then
  echo "✅ brush → $BB"
  echo "--- --help (сверка команды обучения) ---"
  "$BB" --help 2>&1 | head -40
else
  echo "❌ brush-cli не найден. Собрать: cargo build --release -p brush-cli"
  echo "   затем задать BRUSH_BIN=/путь/к/target/release/brush-cli"
fi

line "LM STUDIO (локальный LLM, опционально)"
curl -s -o /dev/null -w "GET /v1/models → %{http_code}\n" http://localhost:1234/v1/models 2>&1 || echo "недоступен (LLM-комментарии будут пропущены, на 3D-билд не влияет)"
echo "--- эмбеддинг-модель для RAG (нужна для индексации/чата по нормам) ---"
EMB_MODEL="text-embedding-nomic-embed-text-v1.5"
EMB_CODE=$(curl -s -o /tmp/_emb.json -w "%{http_code}" http://localhost:1234/v1/embeddings \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"$EMB_MODEL\",\"input\":\"тест\"}" 2>/dev/null || echo "000")
if [ "$EMB_CODE" = "200" ]; then
  echo "✅ эмбеддинги работают ($EMB_MODEL)"
else
  echo "❌ /v1/embeddings → $EMB_CODE. В LM Studio НЕ загружена embedding-модель"
  echo "   ($EMB_MODEL) — без неё индекс норм не собрать и чат по нормам пуст."
fi

line "RAG-ИНДЕКС (ChromaDB — то, что читает чат по нормам)"
PY="./.venv/bin/python"; [ -x "$PY" ] || PY="python3"
"$PY" - <<'PYEOF' 2>/dev/null || echo "не удалось прочитать chroma (нет chromadb в этом python?)"
import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
try:
    import chromadb
    from pathlib import Path
    p = Path("data/chroma_db")
    if not p.exists():
        print("❌ data/chroma_db не существует — индекс не собран"); raise SystemExit
    cl = chromadb.PersistentClient(path=str(p))
    cols = cl.list_collections()
    if not cols:
        print("❌ коллекций нет — индекс не собран")
    for c in cols:
        n = c.count()
        mark = "✅" if n > 100 else "⚠️"
        print(f"  {mark} {c.name}: {n} чанков")
    print("  (чат по нормам читает коллекцию 'uz_construction_norms' — в ней должны быть тысячи)")
except Exception as e:
    print("ошибка:", e)
PYEOF

line "БЭКЕНД (если запущен)"
curl -s -o /dev/null -w "GET /api/gsplat/jobs → %{http_code}\n" http://127.0.0.1:8765/api/gsplat/jobs 2>&1 || echo "не запущен"

printf '\n===== ГОТОВО — скопируй весь вывод выше в чат =====\n'
