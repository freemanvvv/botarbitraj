#!/bin/bash
# Construction AI Copilot — Web App
# Production: один порт (8765), статика через FastAPI
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
FRONTEND_DIR="$SCRIPT_DIR/webapp/frontend"

echo "⚡ Construction AI Copilot"
echo "========================="
echo ""

# 1. Build frontend
echo "📦 Building frontend..."
cd "$FRONTEND_DIR"
npx vite build --quiet 2>/dev/null
echo "   ✅ dist/ готов"

# 2. Activate venv
source "$VENV_DIR/bin/activate"
pip install -q fastapi uvicorn python-multipart pydantic 2>/dev/null

# 3. Start server (один порт для всего)
echo ""
echo "🚀 http://localhost:8765"
echo ""

cd "$SCRIPT_DIR"
python3 -m uvicorn webapp.backend.main:app --host 0.0.0.0 --port 8765 --reload
