import { state } from './state.js'
import { CONFIG } from '../config/index.js'

const TELEGRAM_API = `https://api.telegram.org/bot${CONFIG.BOT_TOKEN || ''}`

/**
 * Отправляет уведомление в Telegram через Bot API.
 */
export async function sendTelegramNotification(text) {
  const chatId = state.getNotifyChatId()
  if (!chatId || !CONFIG.BOT_TOKEN) return

  try {
    await fetch(`${TELEGRAM_API}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        chat_id: chatId,
        text,
        parse_mode: 'HTML',
        disable_web_page_preview: true,
      }),
    })
  } catch (err) {
    console.error('Telegram notification error:', err.message)
  }
}

/**
 * Уведомление о найденной арбитражной возможности.
 * Пример: "🔥 Найден арбитраж! SOL→RAY→USDC: +3.15%"
 */
export function notifyOpportunity(opportunity) {
  const profitBps = opportunity.profitBps || 0
  const pct = opportunity.profitPercent || (profitBps / 100).toFixed(2)

  // Расчёт на примере 100 USDC
  const entryAmount = 100
  const exitAmount = entryAmount * (1 + profitBps / 10000)

  const msg =
    `🔥 <b>Найден арбитраж!</b>\n\n` +
    `📈 <b>${opportunity.route}</b>\n` +
    `💵 Вложил бы: <b>${entryAmount} USDC</b>\n` +
    `💰 Получил бы: <b>${exitAmount.toFixed(2)} USDC</b>\n` +
    `📊 Профит: <b>+${(exitAmount - entryAmount).toFixed(2)} USDC</b> (<code>${pct}%</code>)\n\n` +
    `⏰ ${new Date().toLocaleTimeString('ru-RU', { timeZone: 'Asia/Karachi' })}`

  console.log(`💰 Opportunity: ${opportunity.route} +${pct}%`)
  sendTelegramNotification(msg)
}

/**
 * Уведомление об успешной сделке.
 */
export function notifyTradeExecuted(opportunity, result) {
  const pct = opportunity.profitPercent || '?'
  const txIds = (result || []).map(r => r.txId).filter(Boolean)

  const msg =
    `✅ <b>Сделка исполнена!</b>\n\n` +
    `📈 <b>${opportunity.route}</b>\n` +
    `📊 Профит: <code>${pct}%</code>\n` +
    (txIds.length > 0 ? `🔗 Tx: <code>${txIds.join(', ')}</code>\n` : '') +
    `⏰ ${new Date().toLocaleTimeString('ru-RU', { timeZone: 'Asia/Karachi' })}`

  console.log(`✅ Trade executed: ${opportunity.route} +${pct}%`)
  sendTelegramNotification(msg)
}

/**
 * Уведомление о неудачной сделке.
 */
export function notifyTradeFailed(opportunity, error) {
  const msg =
    `❌ <b>Сделка не удалась</b>\n\n` +
    `📈 ${opportunity.route}\n` +
    `⚠️ Ошибка: <code>${error}</code>\n\n` +
    `⏰ ${new Date().toLocaleTimeString('ru-RU', { timeZone: 'Asia/Karachi' })}`

  console.log(`❌ Trade failed: ${opportunity.route} — ${error}`)
  sendTelegramNotification(msg)
}
