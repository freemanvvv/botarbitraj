import { CONFIG } from '../config/index.js'
import { getQuote } from '../jupiter/client.js'
import { notifyOpportunity, notifyTradeExecuted, notifyTradeFailed } from './notifier.js'
import { state } from './state.js'

let scanTimer = null
let running = false
const USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

const SYMBOL_MAP = {}
for (const [sym, addr] of Object.entries(CONFIG.TOKENS)) {
  SYMBOL_MAP[addr] = sym
}

function tokenSymbol(mint) {
  return SYMBOL_MAP[mint] || mint.slice(0, 6) + '...'
}

// Получить цену токена через quote (1 USDC → token)
async function getTokenPrice(mint) {
  if (mint === USDC_MINT) return 1
  const q = await getQuote(USDC_MINT, mint, BigInt(1_000_000)) // 1 USDC
  if (!q) return null
  return Number(q.outAmount) / 1_000_000 // цена: сколько единиц токена за 1 USDC
}

// Динамический поиск треугольников через quotes
export async function discoverTriangles(amountUsdc = BigInt(CONFIG.TRADE_AMOUNT_USDC) * BigInt(1_000_000)) {
  const tokens = CONFIG.TOP_TOKENS.filter(m => m !== USDC_MINT)
  const candidates = []

  // 1. Получаем цены всех топ-токенов (1 quote на токен)
  const prices = {}
  for (const mint of tokens) {
    const price = await getTokenPrice(mint)
    if (price) prices[mint] = price
  }

  if (Object.keys(prices).length < 2) return []

  // 2. Строим все треугольники и теоретически оцениваем профит
  const tokenList = Object.keys(prices)
  for (let i = 0; i < tokenList.length; i++) {
    for (let j = 0; j < tokenList.length; j++) {
      if (i === j) continue

      const a = tokenList[i]
      const b = tokenList[j]
      const pA = prices[a]
      const pB = prices[b]

      // Теоретическая оценка: 1 USDC → a → b → USDC
      // Leg1: 1 USDC → pA единиц A
      // Leg2: pA единиц A → pA * (pB/pA) = pB единиц B (кросс-курс)
      // Leg3: pB единиц B → pB * (1) = pB USDC
      // Но для точности нужен quote A→B
      // Используем только как начальный фильтр

      const theoreticalBps = 0 // Пропускаем теоретическую оценку, идём сразу к quotes

      candidates.push({
        triangle: [USDC_MINT, a, b],
        route: `USDC → ${tokenSymbol(a)} → ${tokenSymbol(b)} → USDC`,
        tokens: [
          { mint: USDC_MINT, symbol: 'USDC' },
          { mint: a, symbol: tokenSymbol(a) },
          { mint: b, symbol: tokenSymbol(b) },
        ],
        priceA: pA,
        priceB: pB,
        theoreticalBps,
      })
    }
  }

  // 3. Берём топ-8 кандидатов и верифицируем через реальные quotes
  const TOP_N = Math.min(6, candidates.length)
  const shuffled = [...candidates].sort(() => Math.random() - 0.5).slice(0, TOP_N)

  const verified = []

  for (const cand of shuffled) {
    try {
      const result = await simulateTriangle(cand.triangle, amountUsdc)
      if (result) {
        verified.push(result)
      }
    } catch {
      // Тихий пропуск
    }
  }

  // Сортируем по профиту
  verified.sort((a, b) => b.profitBps - a.profitBps)
  return verified
}

// Симуляция треугольника через 3 реальных Jupiter quote
export async function simulateTriangle(triangle, amountUsdc) {
  const [tokenA, tokenB, tokenC] = triangle

  // Leg 1: USDC → tokenB
  const q1 = await getQuote(tokenA, tokenB, amountUsdc.toString())
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

  // Пропускаем если потеря >50%
  if (outUsdc < BigInt(amountUsdc) / BigInt(2)) return null

  const profitBps = Number(((outUsdc - BigInt(amountUsdc)) * BigInt(10000)) / BigInt(amountUsdc))

  return {
    triangle,
    tokens: [
      { mint: tokenA, symbol: tokenSymbol(tokenA) },
      { mint: tokenB, symbol: tokenSymbol(tokenB) },
      { mint: tokenC, symbol: tokenSymbol(tokenC) },
    ],
    route: `${tokenSymbol(tokenA)} → ${tokenSymbol(tokenB)} → ${tokenSymbol(tokenC)} → ${tokenSymbol(tokenA)}`,
    amountIn: Number(amountUsdc) / 1e6,
    amountOut: Number(outUsdc) / 1e6,
    profitBps,
    profitPercent: (profitBps / 100).toFixed(2),
    quotes: [q1, q2, q3],
  }
}

// Полный скан
export async function scanAllTriangles() {
  const results = await discoverTriangles()

  const opportunities = results
    .filter(r => r.profitBps >= CONFIG.MIN_PROFIT_BPS)
    .sort((a, b) => b.profitBps - a.profitBps)

  state.setScanResults(results)
  state.setOpportunities(opportunities)

  return { all: results, profitable: opportunities }
}

// Исполнить треугольную сделку
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

    try {
      const txId = await wallet.signAndSendTransaction(swapTx)
      results.push({ leg: i + 1, txId })
    } catch (err) {
      throw new Error(`Leg ${i + 1} failed: ${err.message}`)
    }
  }

  return results
}

// Авто-трейд
export async function startAutoTrade(wallet) {
  if (running) return
  running = true
  state.setBotStatus('running')
  console.log('🤖 Auto-trade started (dynamic triangle search)')

  const loop = async () => {
    if (!running) return

    try {
      const { profitable } = await scanAllTriangles()
      console.log(`  ↳ Scanned triangles: best profit = ${profitable.length > 0 ? profitable[0].profitBps + ' bps' : 'none'}`)

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
