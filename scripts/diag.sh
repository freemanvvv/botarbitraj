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

line "BRUSH (трейнер 3DGS на Mac)"
if command -v brush >/dev/null 2>&1 || [ -n "$BRUSH_BIN" ]; then
  BB="${BRUSH_BIN:-$(command -v brush)}"
  echo "✅ brush → $BB"
  echo "--- brush --help (нужно, чтобы сверить команду обучения) ---"
  "$BB" --help 2>&1 | head -60
else
  echo "❌ brush не найден. Установи и/или задай BRUSH_BIN=/путь/к/brush"
fi

line "LM STUDIO (локальный LLM, опционально)"
curl -s -o /dev/null -w "GET /v1/models → %{http_code}\n" http://localhost:1234/v1/models 2>&1 || echo "недоступен (LLM-комментарии будут пропущены, на 3D-билд не влияет)"

line "БЭКЕНД (если запущен)"
curl -s -o /dev/null -w "GET /api/gsplat/jobs → %{http_code}\n" http://127.0.0.1:8765/api/gsplat/jobs 2>&1 || echo "не запущен"

printf '\n===== ГОТОВО — скопируй весь вывод выше в чат =====\n'
