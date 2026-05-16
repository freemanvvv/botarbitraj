import { state } from './state.js'

export function notifyOpportunity(opportunity) {
  const msg = `💰 *Opportunity found!*\nRoute: ${opportunity.route}\nProfit: ${opportunity.profitPercent}%\nΔ: ${opportunity.profitBps} bps`
  console.log(msg)
}

export function notifyTradeExecuted(opportunity, result) {
  const msg = `✅ *Trade executed!*\nRoute: ${opportunity.route}\nProfit: ${opportunity.profitPercent}%\nTx: ${result.map(r => r.txId).join(', ')}`
  console.log(msg)
}

export function notifyTradeFailed(opportunity, error) {
  const msg = `❌ *Trade failed!*\nRoute: ${opportunity.route}\nError: ${error}`
  console.log(msg)
}
