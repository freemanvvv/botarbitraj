# SolArb — Solana Triangular Arbitrage Bot 🤖💰

Telegram Mini App для треугольного арбитража на Solana через Jupiter.

## Архитектура

```
solana-arbitrage/
├── backend/       # Node.js + Express + WebSocket
│   └── src/
│       ├── arbitrage/   # Сканер + исполнитель сделок
│       ├── jupiter/     # Клиент Jupiter API
│       ├── config/      # Настройки, токены, треугольники
│       ├── utils/       # Кошелёк, Solana RPC
│       └── server.js    # REST + WebSocket сервер
├── frontend/      # React + Vite (Telegram Mini App)
│   └── src/
│       ├── pages/       # Dashboard, Scanner, Wallet, Bot, Trades
│       ├── hooks/       # WebSocket, API хуки
│       └── styles.css
├── bot/           # Telegram bot wrapper
└── .env.example
```

## Запуск

### 1. Backend
```bash
cd backend
cp ../.env.example .env  # заполни PRIVATE_KEY и RPC
npm install
npm run dev
```

### 2. Frontend
```bash
cd frontend
npm install
npm run dev
```

### 3. Telegram Bot
```bash
cd bot
npm install
BOT_TOKEN=xxx node index.js
```

## Треугольные маршруты

По умолчанию сканируются:
- USDC → SOL → RAY → USDC
- USDC → SOL → JUP → USDC
- USDC → SOL → BONK → USDC
- USDC → RAY → JUP → USDC

Можно добавить свои в `backend/src/config/index.js`.

## API Endpoints

| Method | Path | Описание |
|--------|------|----------|
| GET | /api/prices | Цены токенов |
| GET | /api/scan | Запустить сканирование |
| GET | /api/state | Полное состояние |
| GET | /api/wallet | Статус кошелька |
| POST | /api/wallet/connect | Подключить кошелёк |
| POST | /api/bot/start | Запустить бота |
| POST | /api/bot/stop | Остановить бота |
| GET | /api/trades | История сделок |
| WS | /ws | WebSocket live updates |

## Telegram Bot Commands

- `/start` — открыть Mini App
- `/status` — статус бота
- `/prices` — текущие цены
- `/scan` — ручной скан
- `/trades` — последние сделки
