import { CONFIG } from '../config/index.js'
import { getQuote, getSwapTransaction } from '../jupiter/client.js'
import { notifyOpportunity, notifyTradeExecuted, notifyTradeFailed } from './notifier.js'
import { state } from './state.js'

let scanTimer = null
let running = false

// Calculate simulated output for a 3-leg triangle
// Returns expected USDC back or null
export async function simulateTriangle(triangle, amountUsdc) {
  const [tokenA, tokenB, tokenC] = triangle // A = USDC

  // Leg 1: USDC → tokenB
  const q1 = await getQuote(tokenA, tokenB, amountUsdc)
  if (!q1) return null
  const outB = BigInt(q1.outAmount)

  // Leg 2: tokenB → tokenC
  const q2 = await getQuote(tokenB, tokenC, outB.toString())
  if (!q2) return null
  const outC = BigInt(q2.outAmount)

  // Leg 3: tokenC → USDC
  const q3 = await getQuote(tokenC, tokenA, outC.toString())
  if (!q3) return null
  const outUsdc = BigInt(q3.outAmount)

  const profitBps = Number(((outUsdc - BigInt(amountUsdc)) * BigInt(10000)) / BigInt(amountUsdc))

  return {
    triangle, // token mints
    tokens: [
      { mint: tokenA, symbol: getTokenSymbol(tokenA) },
      { mint: tokenB, symbol: getTokenSymbol(tokenB) },
      { mint: tokenC, symbol: getTokenSymbol(tokenC) },
    ],
    route: `${getTokenSymbol(tokenA)} → ${getTokenSymbol(tokenB)} → ${getTokenSymbol(tokenC)} → ${getTokenSymbol(tokenA)}`,
    amountIn: amountUsdc,
    amountOut: Number(outUsdc) / 1e6,
    profitBps,
    profitPercent: (profitBps / 100).toFixed(2),
    quotes: [q1, q2, q3],
  }
}

// Scan all triangles for opportunities
export async function scanAllTriangles() {
  const results = []

  for (const triangle of CONFIG.TRIANGLES) {
    try {
      const result = await simulateTriangle(triangle, BigInt(CONFIG.TRADE_AMOUNT_USDC) * BigInt(1_000_000)) // USDC has 6 decimals
      if (result) {
        results.push(result)
      }
    } catch (err) {
      // Skip errors silently
    }
  }

  // Filter profitable opportunities
  const opportunities = results
    .filter(r => r.profitBps >= CONFIG.MIN_PROFIT_BPS)
    .sort((a, b) => b.profitBps - a.profitBps)

  state.setScanResults(results)
  state.setOpportunities(opportunities)

  return { all: results, profitable: opportunities }
}

// Execute a trade for a triangle opportunity
export async function executeTrade(opportunity, wallet) {
  if (!wallet?.publicKey) {
    throw new Error('Wallet not connected')
  }

  const { quotes } = opportunity

  const results = []
  for (let i = 0; i < quotes.length; i++) {
    const swapTx = await getSwapTransaction(quotes[i], wallet.publicKey.toBase58())
    if (!swapTx) {
      throw new Error(`Failed to get swap tx for leg ${i + 1}`)
    }

    // Sign and send transaction
    try {
      const txId = await wallet.signAndSendTransaction(swapTx)
      results.push({ leg: i + 1, txId })
    } catch (err) {
      throw new Error(`Leg ${i + 1} failed: ${err.message}`)
    }
  }

  return results
}

// Auto-trade loop
export async function startAutoTrade(wallet) {
  if (running) return
  running = true
  state.setBotStatus('running')

  console.log('🤖 Auto-trade started')

  const loop = async () => {
    if (!running) return

    try {
      const { profitable } = await scanAllTriangles()

      // Execute best opportunity if profitable
      if (profitable.length > 0 && wallet?.publicKey) {
        const best = profitable[0]
        notifyOpportunity(best)

        try {
          const result = await executeTrade(best, wallet)
          notifyTradeExecuted(best, result)
          state.addTrade(best, result)
        } catch (err) {
          notifyTradeFailed(best, err.message)
        }
      }
    } catch (err) {
      console.error('Scan error:', err.message)
    }

    if (running) {
      scanTimer = setTimeout(loop, CONFIG.SCAN_INTERVAL_MS)
    }
  }

  loop()
}

export function stopAutoTrade() {
  running = false
  if (scanTimer) {
    clearTimeout(scanTimer)
    scanTimer = null
  }
  state.setBotStatus('idle')
  console.log('🤖 Auto-trade stopped')
}

export function isRunning() {
  return running
}

// Helpers
const tokenSymbols = {}
for (const [key, addr] of Object.entries(CONFIG.TOKENS)) {
  tokenSymbols[addr] = key
}

function getTokenSymbol(mint) {
  return tokenSymbols[mint] || mint.slice(0, 8) + '...'
}
