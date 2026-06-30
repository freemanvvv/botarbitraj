#!/bin/bash
# ============================================================
# Construction AI Copilot — Установка на Mac M3 (Apple Silicon)
# Запуск: bash scripts/setup_mac_m3.sh
# ============================================================
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
info() { echo -e "${CYAN}→ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
err()  { echo -e "${RED}❌ $1${NC}"; }
hdr()  { echo -e "\n${BOLD}$1${NC}"; echo "$(printf '─%.0s' {1..50})"; }

# ── Корневая папка проекта ────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
info "Проект: $PROJECT_DIR"

# ═══════════════════════════════════════════════════════════
hdr "1/6  Homebrew"
# ═══════════════════════════════════════════════════════════
if ! command -v brew &>/dev/null; then
    info "Устанавливаю Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Добавляем brew в PATH для Apple Silicon
    eval "$(/opt/homebrew/bin/brew shellenv)"
else
    ok "Homebrew уже установлен: $(brew --version | head -1)"
fi

# ═══════════════════════════════════════════════════════════
hdr "2/6  Системные зависимости (ffmpeg · colmap · node)"
# ═══════════════════════════════════════════════════════════
BREW_PKGS=()
for pkg in ffmpeg colmap node; do
    if brew list "$pkg" &>/dev/null 2>&1; then
        ok "$pkg уже установлен"
    else
        BREW_PKGS+=("$pkg")
    fi
done

if [ ${#BREW_PKGS[@]} -gt 0 ]; then
    info "Устанавливаю через brew: ${BREW_PKGS[*]}"
    brew install "${BREW_PKGS[@]}"
fi

ok "ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
ok "colmap: $(colmap -h 2>&1 | head -1 || echo 'установлен')"
ok "node: $(node --version)"

# ═══════════════════════════════════════════════════════════
hdr "3/6  Python — виртуальное окружение"
# ═══════════════════════════════════════════════════════════

# Проверяем Python 3.10+
PY=$(command -v python3.11 || command -v python3.10 || command -v python3)
PY_VER=$("$PY" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python: $PY_VER ($PY)"

if [[ "$PY_VER" < "3.10" ]]; then
    warn "Рекомендуется Python 3.10+. Устанавливаю через brew..."
    brew install python@3.11
    PY="$(brew --prefix)/bin/python3.11"
fi

# Создаём venv если нет
VENV="$PROJECT_DIR/.venv"
if [ ! -d "$VENV" ]; then
    info "Создаю виртуальное окружение: $VENV"
    "$PY" -m venv "$VENV"
fi

source "$VENV/bin/activate"
ok "venv активирован: $(which python)"
python -m pip install --upgrade pip --quiet

# ═══════════════════════════════════════════════════════════
hdr "4/6  Python-зависимости проекта"
# ═══════════════════════════════════════════════════════════

info "Устанавливаю requirements.txt..."
pip install -r "$PROJECT_DIR/requirements.txt" --quiet

info "Устанавливаю зависимости бэкенда..."
pip install -r "$PROJECT_DIR/webapp/backend/requirements.txt" --quiet

ok "Основные зависимости установлены"

# ═══════════════════════════════════════════════════════════
hdr "5/6  Nerfstudio (Gaussian Splatting для Apple Silicon)"
# ═══════════════════════════════════════════════════════════

# PyTorch с поддержкой MPS (Metal Performance Shaders) — для M1/M2/M3
if python -c "import torch; torch.backends.mps.is_available()" 2>/dev/null | grep -q True 2>/dev/null; then
    ok "PyTorch с MPS уже установлен"
else
    info "Устанавливаю PyTorch (MPS для Apple Silicon)..."
    pip install torch torchvision torchaudio --quiet
    # Проверяем MPS
    python -c "
import torch
mps = torch.backends.mps.is_available()
print(f'MPS доступен: {mps}')
if not mps:
    print('Предупреждение: MPS недоступен. Обучение будет медленнее (CPU).')
"
fi

# Nerfstudio
if command -v ns-train &>/dev/null; then
    ok "Nerfstudio уже установлен: $(ns-train --version 2>/dev/null || echo 'установлен')"
else
    info "Устанавливаю Nerfstudio..."
    pip install nerfstudio --quiet
    ok "Nerfstudio установлен"
fi

# Проверяем ns-train
if ! command -v ns-train &>/dev/null; then
    # Добавляем pip bin в PATH
    PIP_BIN=$(python -m site --user-base)/bin
    export PATH="$PATH:$PIP_BIN:$VENV/bin"
fi

echo ""
warn "Примечание: На Mac M3 Nerfstudio использует Metal GPU (MPS)."
warn "Обучение сцены займёт ~30-60 минут (vs 5-10 мин на NVIDIA)."
warn "20 ГБ unified memory M3 — достаточно для большинства сцен."

# ═══════════════════════════════════════════════════════════
hdr "6/6  Frontend (npm)"
# ═══════════════════════════════════════════════════════════

FRONTEND_DIR="$PROJECT_DIR/webapp/frontend"
info "npm install в $FRONTEND_DIR ..."
cd "$FRONTEND_DIR"
npm install --silent
ok "npm зависимости установлены (включая @mkkellogg/gaussian-splats-3d)"
cd "$PROJECT_DIR"

# ═══════════════════════════════════════════════════════════
hdr "Итог — Как запустить"
# ═══════════════════════════════════════════════════════════

echo ""
echo -e "${GREEN}✅ Установка завершена!${NC}"
echo ""
echo -e "${BOLD}Запуск приложения:${NC}"
echo ""
echo -e "  ${CYAN}# Терминал 1 — бэкенд${NC}"
echo -e "  source .venv/bin/activate"
echo -e "  cd webapp/backend && uvicorn main:app --reload --port 8765"
echo ""
echo -e "  ${CYAN}# Терминал 2 — фронтенд (для разработки)${NC}"
echo -e "  cd webapp/frontend && npm run dev"
echo ""
echo -e "  ${CYAN}# Открыть в браузере${NC}"
echo -e "  http://localhost:5173"
echo ""
echo -e "${BOLD}Также нужно:${NC}"
echo -e "  • LM Studio — скачай с lmstudio.ai, загрузи Qwen3-14B"
echo -e "  • Запусти Local Server в LM Studio (порт 1234)"
echo ""

# ── Проверка установки ────────────────────────────────────
echo -e "${BOLD}Проверка компонентов:${NC}"
source "$VENV/bin/activate"

check() {
    local name="$1"; local cmd="$2"
    if eval "$cmd" &>/dev/null; then ok "$name"
    else err "$name — не найден"; fi
}

check "ffmpeg"      "ffmpeg -version"
check "colmap"      "colmap -h"
check "Python venv" "python -c 'import sys; assert sys.prefix != sys.base_prefix'"
check "FastAPI"     "python -c 'import fastapi'"
check "ChromaDB"    "python -c 'import chromadb'"
check "ifcopenshell" "python -c 'import ifcopenshell'"
check "requests"    "python -c 'import requests'"
check "PyTorch"     "python -c 'import torch'"
check "Nerfstudio"  "command -v ns-train"
check "Node.js"     "node --version"
check "npm splats"  "test -d '$FRONTEND_DIR/node_modules/@mkkellogg'"

echo ""
