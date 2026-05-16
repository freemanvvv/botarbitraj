import TelegramBot from 'node-telegram-bot-api'
import fetch from 'node-fetch'

const BOT_TOKEN = process.env.BOT_TOKEN
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:3001'
const MINI_APP_URL = process.env.MINI_APP_URL || 'https://your-mini-app.com'

if (!BOT_TOKEN) {
  console.error('❌ BOT_TOKEN required')
  process.exit(1)
}

const bot = new TelegramBot(BOT_TOKEN, { polling: true })

// ─── Commands ───

// /start — sends Mini App button
bot.onText(/\/start/, (msg) => {
  const chatId = msg.chat.id

  bot.sendMessage(chatId, '🤖 *SolArb — Solana Arbitrage Bot*\n\nTriangular arbitrage scanner & executor on Solana via Jupiter.\n\n', {
    parse_mode: 'Markdown',
    reply_markup: {
      inline_keyboard: [
        [{ text: '🚀 Open Mini App', web_app: { url: MINI_APP_URL } }],
      ],
    },
  })
})

// /status — bot status
bot.onText(/\/status/, async (msg) => {
  const chatId = msg.chat.id
  try {
    const res = await fetch(`${BACKEND_URL}/api/bot/status`)
    const data = await res.json()
    const status = data.running ? '🟢 Running' : '⚪ Idle'
    bot.sendMessage(chatId, `*Bot Status:* ${status}\n*Interval:* 3s`, { parse_mode: 'Markdown' })
  } catch (err) {
    bot.sendMessage(chatId, `❌ Backend unreachable: ${err.message}`)
  }
})

// /prices — current token prices
bot.onText(/\/prices/, async (msg) => {
  const chatId = msg.chat.id
  try {
    const res = await fetch(`${BACKEND_URL}/api/prices`)
    const prices = await res.json()
    const lines = Object.entries(prices).map(([sym, data]) =>
      `${sym}: $${data.price ? parseFloat(data.price).toFixed(6) : '—'}`
    )
    bot.sendMessage(chatId, `*Token Prices*\n\n${lines.join('\n')}`, { parse_mode: 'Markdown' })
  } catch (err) {
    bot.sendMessage(chatId, `❌ ${err.message}`)
  }
})

// /scan — manual scan
bot.onText(/\/scan/, async (msg) => {
  const chatId = msg.chat.id
  await bot.sendMessage(chatId, '🔍 Scanning...')

  try {
    const res = await fetch(`${BACKEND_URL}/api/scan`)
    const data = await res.json()
    const profitable = data.profitable || []
    const all = data.all || []

    if (profitable.length > 0) {
      const lines = profitable.map(o =>
        `🔥 *${o.route}* — +${o.profitPercent}% (${o.profitBps} bps)`
      )
      bot.sendMessage(chatId, `*Opportunities Found!*\n\n${lines.join('\n')}`, { parse_mode: 'Markdown' })
    } else {
      bot.sendMessage(chatId, '❌ No profitable opportunities right now.')
    }
  } catch (err) {
    bot.sendMessage(chatId, `❌ Scan failed: ${err.message}`)
  }
})

// /trades — last trades
bot.onText(/\/trades/, async (msg) => {
  const chatId = msg.chat.id
  try {
    const res = await fetch(`${BACKEND_URL}/api/trades`)
    const trades = await res.json()

    if (trades.length === 0) {
      bot.sendMessage(chatId, 'No trades yet.')
      return
    }

    const lines = trades.slice(-5).reverse().map(t =>
      `*${t.route}* — +${t.profitPercent}%\n└ ${new Date(t.timestamp).toLocaleTimeString()}`
    )
    bot.sendMessage(chatId, `*Last Trades*\n\n${lines.join('\n\n')}`, { parse_mode: 'Markdown' })
  } catch (err) {
    bot.sendMessage(chatId, `❌ ${err.message}`)
  }
})

console.log('🤖 Telegram bot started')
