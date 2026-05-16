import { CONFIG } from '../config/index.js'
import { getQuote, getPrices } from '../jupiter/client.js'
import { notifyOpportunity, notifyTradeExecuted, notifyTradeFailed } from './notifier.js'
import { state } from './state.js'

let scanTimer = null
let running = false
const USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

// ─── Алгоритм динамического поиска треугольников ───

// Символ → mint (для читаемых названий)
const SYMBOL_MAP = {}
for (const [sym, addr] of Object.entries(CONFIG.TOKENS)) {
  SYMBOL_MAP[addr] = sym
}

function tokenSymbol(mint) {
  return SYMBOL_MAP[mint] || mint.slice(0, 6) + '...'
}

// Быстрый поиск по ценам: запрашиваем цены Jupiter для всех топ-токенов
// Потом математически вычисляем потенциальные треугольники
export async function discoverTriangles(amountUsdc = BigInt(CONFIG.TRADE_AMOUNT_USDC) * BigInt(1_000_000)) {
  // 1. Берём цены всех токенов через Jupiter Price API (один запрос)
  const priceData = await getPrices(CONFIG.TOP_TOKENS)
  if (!priceData) return []

  const candidates = []

  // 2. Строим все треугольники USDC → A → B → USDC
  // Берём токены без USDC (он уже база)
  const tokens = CONFIG.TOP_TOKENS.filter(m => m !== USDC_MINT)

  for (let i = 0; i < tokens.length; i++) {
    for (let j = 0; j < tokens.length; j++) {
      if (i === j) continue // A != B

      const mintA = tokens[i]
      const mintB = tokens[j]

      // Цены от Jupiter
      // price(A in USDC) — сколько USDC за 1 A
      // price(B in USDC) — сколько USDC за 1 B
      const priceUSDC_per_A = priceData[mintA]?.price
      const priceUSDC_per_B = priceData[mintB]?.price

      if (!priceUSDC_per_A || !priceUSDC_per_B) continue

      // Математика:
      // 1 USDC → (1 / priceUSDC_per_A) единиц A
      // A → B: через их стоимости в USDC: priceUSDC_per_A / priceUSDC_per_B единиц B (здесь упрощение)
      // Но на самом деле цена A→B на Jupiter может отличаться от кросс-курса через USDC
      // Используем цены только как НАЧАЛЬНЫЙ ФИЛЬТР, дальше верифицируем через quote

      // Теоретический профит (грубая оценка)
      // Можно получить quote A→B и B→USDC через Jupiter для точности
      // Но для отсеивания используем цены

      candidates.push({
        triangle: [USDC_MINT, mintA, mintB], // USDC → A → B → USDC
        route: `USDC → ${tokenSymbol(mintA)} → ${tokenSymbol(mintB)} → USDC`,
        tokens: [
          { mint: USDC_MINT, symbol: 'USDC' },
          { mint: mintA, symbol: tokenSymbol(mintA) },
          { mint: mintB, symbol: tokenSymbol(mintB) },
        ],
        priceA: priceUSDC_per_A,
        priceB: priceUSDC_per_B,
      })
    }
  }

  // 3. Берём топ-15 кандидатов (или меньше) и верифицируем через реальные quotes
  const TOP_N = Math.min(15, candidates.length)
  // Сортируем случайно — разные треугольники дают шанс найти профит
  const shuffled = candidates.sort(() => Math.random() - 0.5).slice(0, TOP_N)

  // Верификация
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

// Симуляция треугольника через реальные Jupiter quotes
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

  // Не включаем сделки с потерей >50%
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

// Полный скан: динамический поиск + верификация
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
