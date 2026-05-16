import express from 'express'
import cors from 'cors'
import { createServer } from 'http'
import { WebSocketServer } from 'ws'
import { CONFIG } from './config/index.js'
import { getPrices } from './jupiter/client.js'
import { scanAllTriangles, startAutoTrade, stopAutoTrade, isRunning } from './arbitrage/scanner.js'
import { state, setWssClients } from './arbitrage/state.js'
import { loadWallet, getWallet, signAndSendTransaction, getWalletBalance, getTokenBalance } from './utils/wallet.js'
import * as binance from './arbitrage/binance.js'

const app = express()
app.use(cors({
  origin: ['http://localhost:5173', 'https://freemanvvv.github.io', 'https://solarb-frontend.onrender.com'],
  credentials: true,
}))
app.use(express.json())

const server = createServer(app)
// Accept WS connections at /api/ws (used by production frontend)
const wss = new WebSocketServer({ server, path: '/api/ws' })

// WebSocket: keep track of clients
const clients = new Set()
wss.on('connection', (ws) => {
  clients.add(ws)
  setWssClients([...clients])

  // Send current state on connect
  ws.send(JSON.stringify({ type: 'state', payload: state.getFullState() }))

  ws.on('close', () => {
    clients.delete(ws)
    setWssClients([...clients])
  })
})

// --- REST API ---

// Get prices for tracked tokens
app.get('/api/prices', async (req, res) => {
  try {
    const mints = Object.values(CONFIG.TOKENS)
    const prices = await getPrices(mints)
    // Add symbols
    const tokenList = {}
    for (const [symbol, mint] of Object.entries(CONFIG.TOKENS)) {
      tokenList[symbol] = {
        symbol,
        mint,
        price: prices[mint]?.price || null,
      }
    }
    res.json(tokenList)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Get scan results
app.get('/api/scan', async (req, res) => {
  try {
    const results = await scanAllTriangles()
    res.json(results)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Get state
app.get('/api/state', (req, res) => {
  res.json(state.getFullState())
})

// Connect wallet (upload private key)
app.post('/api/wallet/connect', async (req, res) => {
  try {
    const { privateKey } = req.body
    if (!privateKey) {
      return res.status(400).json({ error: 'Private key required' })
    }

    const wallet = loadWallet(privateKey)
    if (!wallet) {
      return res.status(400).json({ error: 'Invalid private key' })
    }

    const balance = await getWalletBalance()
    state.setWalletConnected(true, wallet.publicKey.toBase58())

    res.json({
      connected: true,
      address: wallet.publicKey.toBase58(),
      balanceSol: balance,
    })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Wallet status
app.get('/api/wallet', async (req, res) => {
  const wallet = getWallet()
  if (!wallet) {
    return res.json({ connected: false })
  }

  const balance = await getWalletBalance()
  res.json({
    connected: true,
    address: wallet.publicKey.toBase58(),
    balanceSol: balance,
  })
})

// ─── Binance API ───

// Configure Binance API
app.post('/api/binance/configure', async (req, res) => {
  try {
    const { apiKey: key, secretKey: secret } = req.body
    if (!key || !secret) {
      return res.status(400).json({ error: 'API key and secret required' })
    }
    binance.configure(key, secret)
    state.setBinanceConfigured(true)
    res.json({ configured: true })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Get Binance prices + CEX-DEX spreads
app.get('/api/binance/spreads', async (req, res) => {
  try {
    const prices = await binance.getPrices()
    const mints = Object.values(CONFIG.TOKENS)
    const jupiterRes = await fetch(`${CONFIG.JUPITER_PRICE_API}?ids=${mints.join(',')}`)
    const jupiterData = await jupiterRes.json()

    // Map symbols to tokens
    const symbolMap = {
      SOLUSDT: 'SOL',
      RAYUSDT: 'RAY',
      JUPUSDT: 'JUP',
      BONKUSDT: 'BONK',
      PYTHUSDT: 'PYTH',
    }

    const spreads = {}
    for (const [symbol, binancePrice] of Object.entries(prices)) {
      const token = symbolMap[symbol]
      if (!token) continue
      const mint = CONFIG.TOKENS[token]
      const jupiterPrice = parseFloat(jupiterData.data?.[mint]?.price || 0)

      spreads[token] = {
        binance: binancePrice,
        jupiter: jupiterPrice || null,
        spreadBps: jupiterPrice ? Math.round(((jupiterPrice - binancePrice) / binancePrice) * 10000) : null,
      }
    }

    res.json(spreads)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Binance balance
app.get('/api/binance/balance', async (req, res) => {
  try {
    if (!binance.isConfigured()) {
      return res.json({ connected: false })
    }
    const account = await binance.getBalance()
    const balances = account.balances
      .filter(b => parseFloat(b.free) > 0 || parseFloat(b.locked) > 0)
      .map(b => ({
        asset: b.asset,
        free: parseFloat(b.free),
        locked: parseFloat(b.locked),
        total: parseFloat(b.free) + parseFloat(b.locked),
      }))
    res.json({ connected: true, balances })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/binance/status', (req, res) => {
  res.json({ configured: binance.isConfigured() })
})

// Start bot
app.post('/api/bot/start', async (req, res) => {
  try {
    const wallet = getWallet()
    if (!wallet) {
      return res.status(400).json({ error: 'Wallet not connected' })
    }

    await startAutoTrade(wallet)
    res.json({ status: 'running' })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

// Stop bot
app.post('/api/bot/stop', (req, res) => {
  stopAutoTrade()
  res.json({ status: 'idle' })
})

// Bot status
app.get('/api/bot/status', (req, res) => {
  res.json({
    running: isRunning(),
    status: state.getBotStatus(),
  })
})

// Trade history
app.get('/api/trades', (req, res) => {
  res.json(state.getTrades())
})

server.listen(CONFIG.PORT, () => {
  console.log(`
╔══════════════════════════════════════╗
║   Solana Arbitrage Bot v1            ║
║   Server: http://localhost:${CONFIG.PORT}         ║
║   WebSocket: ws://localhost:${CONFIG.PORT}        ║
╚══════════════════════════════════════╝
  `)
})
