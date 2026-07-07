#!/bin/bash
# Локальная установка SuperSplat (редактор/чистка Gaussian Splat, MIT,
# github.com/playcanvas/supersplat) — один раз. Клонирует, собирает и кладёт
# статику в webapp/supersplat/dist, откуда её отдаёт бэкенд на /supersplat.
#
# Запуск:  bash scripts/setup_supersplat.sh
# Нужен:   node + npm (те же, что и для фронта). Занимает несколько минут.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD_DIR="$ROOT/webapp/supersplat"          # сюда кладём dist
SRC_DIR="${SUPERSPLAT_SRC:-$ROOT/.supersplat-src}"   # где собираем (не в git)

command -v npm >/dev/null 2>&1 || { echo "❌ Нужен Node.js/npm (brew install node)"; exit 1; }

echo "▶ 1/4 Клонирую SuperSplat → $SRC_DIR"
if [ -d "$SRC_DIR/.git" ]; then
  git -C "$SRC_DIR" pull --ff-only || true
else
  git clone --depth 1 https://github.com/playcanvas/supersplat.git "$SRC_DIR"
fi

echo "▶ 2/4 npm install (может занять пару минут)"
( cd "$SRC_DIR" && npm install )

echo "▶ 3/4 npm run build"
( cd "$SRC_DIR" && npm run build )

echo "▶ 4/4 Копирую dist → $BUILD_DIR/dist"
rm -rf "$BUILD_DIR/dist"
mkdir -p "$BUILD_DIR"
cp -R "$SRC_DIR/dist" "$BUILD_DIR/dist"

echo ""
echo "✅ Готово. Перезапусти сервер — SuperSplat будет на /supersplat,"
echo "   а в 3D-вкладке у моделей появится кнопка «✂️ Очистить в SuperSplat»."
