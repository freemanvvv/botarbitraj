import express from 'express'
import cors from 'cors'
import { createServer } from 'http'
import { WebSocketServer } from 'ws'
import { CONFIG } from './config/index.js'
import { getPrices } from './jupiter/client.js'
import { scanAllTriangles, startAutoTrade, stopAutoTrade, isRunning } from './arbitrage/scanner.js'
import { state, setWssClients, setNotifyChatId } from './arbitrage/state.js'
import { loadWallet, getWallet, signAndSendTransaction, getWalletBalance, getTokenBalance } from './utils/wallet.js'
import * as binance from './arbitrage/binance.js'
import { sendTelegramNotification } from './arbitrage/notifier.js'

const app = express()
app.use(cors({
  origin: ['http://localhost:5173', 'https://freemanvvv.github.io', 'https://solarb-frontend.onrender.com'],
  credentials: true,
}))
app.use(express.json())

const server = createServer(app)
const wss = new WebSocketServer({ server, path: '/api/ws' })

const clients = new Set()
wss.on('connection', (ws) => {
  clients.add(ws)
  setWssClients([...clients])
  ws.send(JSON.stringify({ type: 'state', payload: state.getFullState() }))
  ws.on('close', () => {
    clients.delete(ws)
    setWssClients([...clients])
  })
})

// --- REST API ---

app.get('/api/prices', async (req, res) => {
  try {
    const mints = Object.values(CONFIG.TOKENS)
    const prices = await getPrices(mints)
    const tokenList = {}
    for (const [symbol, mint] of Object.entries(CONFIG.TOKENS)) {
      tokenList[symbol] = { symbol, mint, price: prices[mint]?.price || null }
    }
    res.json(tokenList)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/scan', async (req, res) => {
  try {
    const results = await scanAllTriangles()
    res.json(results)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/state', (req, res) => {
  res.json(state.getFullState())
})

app.post('/api/wallet/connect', async (req, res) => {
  try {
    const { privateKey } = req.body
    if (!privateKey) return res.status(400).json({ error: 'Private key required' })
    const wallet = loadWallet(privateKey)
    if (!wallet) return res.status(400).json({ error: 'Invalid private key' })
    const balance = await getWalletBalance()
    state.setWalletConnected(true, wallet.publicKey.toBase58())
    res.json({ connected: true, address: wallet.publicKey.toBase58(), balanceSol: balance })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/wallet', async (req, res) => {
  const wallet = getWallet()
  if (!wallet) return res.json({ connected: false })
  const balance = await getWalletBalance()
  res.json({ connected: true, address: wallet.publicKey.toBase58(), balanceSol: balance })
})

// ─── Binance API ───

app.post('/api/binance/configure', async (req, res) => {
  try {
    const { apiKey: key, secretKey: secret } = req.body
    if (!key || !secret) return res.status(400).json({ error: 'API key and secret required' })
    binance.configure(key, secret)
    state.setBinanceConfigured(true)
    res.json({ configured: true })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/binance/spreads', async (req, res) => {
  try {
    const prices = await binance.getPrices()
    const mints = Object.values(CONFIG.TOKENS)
    const jupiterRes = await fetch(CONFIG.JUPITER_API + '/quote?inputMint=' + CONFIG.TOKENS.USDC + '&outputMint=' + CONFIG.TOKENS.SOL + '&amount=1000000&slippageBps=50')
    const jupiterData = await jupiterRes.json()
    res.json({ binance: prices, jupiter: jupiterData })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/binance/spreads', async (req, res) => {
  try {
    const prices = await binance.getPrices()
    const symbolMap = { SOLUSDT: 'SOL', RAYUSDT: 'RAY', JUPUSDT: 'JUP', BONKUSDT: 'BONK' }
    const spreads = {}
    for (const [symbol, binancePrice] of Object.entries(prices)) {
      const token = symbolMap[symbol]
      if (!token) continue
      const dexPrice = await getPrices([CONFIG.TOKENS[token]])
      spreads[token] = {
        binance: binancePrice,
        jupiter: parseFloat(dexPrice[CONFIG.TOKENS[token]]?.price || 0),
      }
    }
    res.json(spreads)
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/binance/balance', async (req, res) => {
  try {
    if (!binance.isConfigured()) return res.json({ connected: false })
    const account = await binance.getBalance()
    const balances = account.balances
      .filter(b => parseFloat(b.free) > 0 || parseFloat(b.locked) > 0)
      .map(b => ({ asset: b.asset, free: parseFloat(b.free), locked: parseFloat(b.locked), total: parseFloat(b.free) + parseFloat(b.locked) }))
    res.json({ connected: true, balances })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/api/binance/status', (req, res) => {
  res.json({ configured: binance.isConfigured() })
})

// ─── Telegram Notifications ───

app.post('/api/notifications/configure', (req, res) => {
  const { chatId } = req.body
  if (!chatId) return res.status(400).json({ error: 'chatId required' })
  setNotifyChatId(chatId)
  console.log('Telegram notifications enabled for chat ' + chatId)
  sendTelegramNotification('Уведомления включены! Буду присылать находки арбитража.')
  res.json({ enabled: true, chatId })
})

app.post('/api/notifications/disable', (req, res) => {
  setNotifyChatId(null)
  console.log('Telegram notifications disabled')
  res.json({ enabled: false })
})

app.get('/api/notifications/status', (req, res) => {
  res.json({ enabled: !!state.getNotifyChatId(), chatId: state.getNotifyChatId() })
})

// Start bot
app.post('/api/bot/start', async (req, res) => {
  try {
    const wallet = getWallet()
    if (!wallet) return res.status(400).json({ error: 'Wallet not connected' })
    await startAutoTrade(wallet)
    res.json({ status: 'running' })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.post('/api/bot/stop', (req, res) => {
  stopAutoTrade()
  res.json({ status: 'idle' })
})

app.get('/api/bot/status', (req, res) => {
  res.json({ running: isRunning(), status: state.getBotStatus() })
})

app.get('/api/trades', (req, res) => {
  res.json(state.getTrades())
})

server.listen(CONFIG.PORT, () => {
  console.log('Solana Arbitrage Bot running on port ' + CONFIG.PORT)
})
