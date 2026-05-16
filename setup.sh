#!/usr/bin/env bash
set -e

echo "╔══════════════════════════════════════╗"
echo "║   SolArb — Setup Script             ║"
echo "╚══════════════════════════════════════╝"

# Check deps
command -v node >/dev/null 2>&1 || { echo "❌ Node.js required"; exit 1; }

# Install backend
echo ""
echo "📦 Installing backend..."
cd backend
npm install
cd ..

# Install frontend
echo "📦 Installing frontend..."
cd frontend
npm install
cd ..

# Create .env if not exists
if [ ! -f backend/.env ]; then
  cp .env.example backend/.env
  echo "📝 Created backend/.env — fill in your PRIVATE_KEY and RPC"
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "  ▶ Start backend:  cd backend && npm run dev"
echo "  ▶ Start frontend: cd frontend && npm run dev"
echo ""
echo "  Backend:  http://localhost:3001"
echo "  Frontend: http://localhost:5173"
