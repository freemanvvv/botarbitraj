import TelegramBot from 'node-telegram-bot-api'
import fetch from 'node-fetch'

const BOT_TOKEN = process.env.BOT_TOKEN
const BACKEND_URL = process.env.BACKEND_URL || 'http://localhost:3001'
const MINI_APP_URL = process.env.MINI_APP_URL || 'https://freemanvvv.github.io/botarbitraj'

if (!BOT_TOKEN) {
  console.error('❌ BOT_TOKEN required')
  process.exit(1)
}

const bot = new TelegramBot(BOT_TOKEN, { polling: true })

// ─── Commands ───

// /start
bot.onText(/\/start/, (msg) => {
  const chatId = msg.chat.id
  const userName = msg.from?.first_name || ''

  bot.sendMessage(chatId,
    `👋 <b>Привет, ${userName}!</b>\n\n` +
    `Это <b>SolArb</b> — бот для поиска треугольного арбитража на Solana.\n` +
    `Сканируем DEX через Jupiter, ищем ценовые неэффективности.\n\n` +
    `📌 <b>Команды:</b>\n` +
    `/start — это меню\n` +
    `/scan — ручной скан\n` +
    `/status — статус\n` +
    `/prices — цены токенов\n` +
    `/trades — история сделок\n` +
    `/notify — вкл уведомления о находках\n` +
    `/notify_off — выкл уведомления\n\n` +
    `<a href="${MINI_APP_URL}">🚀 Открыть Mini App</a>`,
    { parse_mode: 'HTML' }
  )
})

// /status
bot.onText(/\/status/, async (msg) => {
  const chatId = msg.chat.id
  try {
    const [statusRes, notifyRes] = await Promise.all([
      fetch(`${BACKEND_URL}/api/bot/status`),
      fetch(`${BACKEND_URL}/api/notifications/status`),
    ])
    const status = await statusRes.json()
    const notify = await notifyRes.json()

    bot.sendMessage(chatId,
      `🤖 <b>Статус бота</b>\n\n` +
      `Статус: ${status.running ? '🟢 Запущен' : '⚪ Остановлен'}\n` +
      `Уведомления: ${notify.enabled ? '🟢 Вкл' : '🔴 Выкл'}`,
      { parse_mode: 'HTML' }
    )
  } catch (err) {
    bot.sendMessage(chatId, `❌ Бэкенд недоступен: ${err.message}`)
  }
})

// /prices
bot.onText(/\/prices/, async (msg) => {
  const chatId = msg.chat.id
  try {
    const res = await fetch(`${BACKEND_URL}/api/prices`)
    const prices = await res.json()
    const lines = Object.entries(prices).map(([sym, data]) => {
      return `${sym}: <b>$${data.price ? parseFloat(data.price).toFixed(6) : '—'}</b>`
    })
    bot.sendMessage(chatId, `<b>💹 Цены токенов</b>\n\n${lines.join('\n')}`, { parse_mode: 'HTML' })
  } catch (err) {
    bot.sendMessage(chatId, `❌ ${err.message}`)
  }
})

// /scan
bot.onText(/\/scan/, async (msg) => {
  const chatId = msg.chat.id
  const sent = await bot.sendMessage(chatId, '🔍 Сканирую...')

  try {
    const res = await fetch(`${BACKEND_URL}/api/scan`)
    const data = await res.json()
    const profitable = data.profitable || []
    const all = data.all || []

    if (profitable.length > 0) {
      const lines = profitable.map(o => {
        const pct = o.profitPercent || '0.00'
        const exitAmt = 100 * (1 + parseFloat(pct) / 100)
        return `🔥 <b>${o.route}</b>\n   100 USDC → <b>${exitAmt.toFixed(2)} USDC</b> (<code>+${pct}%</code>)`
      })
      await bot.editMessageText(
        `<b>🔥 Найдено возможностей: ${profitable.length}</b>\n\n${lines.join('\n\n')}`,
        { chat_id: chatId, message_id: sent.message_id, parse_mode: 'HTML' }
      )
    } else {
      await bot.editMessageText(
        `❌ Прибыльных треугольников не найдено.\nПроверено: ${all.length} комбинаций`,
        { chat_id: chatId, message_id: sent.message_id }
      )
    }
  } catch (err) {
    await bot.editMessageText(`❌ ${err.message}`, { chat_id: chatId, message_id: sent.message_id })
  }
})

// /trades
bot.onText(/\/trades/, async (msg) => {
  const chatId = msg.chat.id
  try {
    const res = await fetch(`${BACKEND_URL}/api/trades`)
    const trades = await res.json()

    if (trades.length === 0) {
      return bot.sendMessage(chatId, '📭 Сделок пока нет.')
    }

    const lines = trades.slice(-5).reverse().map(t => {
      const pct = t.profitPercent || 0
      const time = new Date(t.timestamp).toLocaleTimeString('ru-RU', { timeZone: 'Asia/Karachi' })
      return `📊 <b>${t.route}</b>\n   +${pct}% · ${time}`
    })
    bot.sendMessage(chatId, `<b>📊 Последние сделки</b>\n\n${lines.join('\n\n')}`, { parse_mode: 'HTML' })
  } catch (err) {
    bot.sendMessage(chatId, `❌ ${err.message}`)
  }
})

// /notify — включить уведомления
bot.onText(/\/notify(_on)?$/, async (msg) => {
  const chatId = msg.chat.id
  try {
    const res = await fetch(`${BACKEND_URL}/api/notifications/configure`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chatId }),
    })
    const data = await res.json()
    if (data.enabled) {
      bot.sendMessage(chatId, '🔔 <b>Уведомления включены!</b>\n\n' +
        'Теперь я буду присылать сообщения, когда найду арбитражные возможности.', { parse_mode: 'HTML' })
    }
  } catch (err) {
    bot.sendMessage(chatId, `❌ Ошибка: ${err.message}`)
  }
})

// /notify_off — выключить уведомления
bot.onText(/\/notify_off$/, async (msg) => {
  const chatId = msg.chat.id
  try {
    await fetch(`${BACKEND_URL}/api/notifications/disable`, { method: 'POST' })
    bot.sendMessage(chatId, '🔕 Уведомления выключены.')
  } catch (err) {
    bot.sendMessage(chatId, `❌ Ошибка: ${err.message}`)
  }
})

console.log('🤖 Telegram bot started (commands: /start, /scan, /status, /prices, /trades, /notify, /notify_off)')
