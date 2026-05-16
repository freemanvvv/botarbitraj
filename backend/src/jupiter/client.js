import fetch from 'node-fetch'
import { CONFIG } from '../config/index.js'

const USDC_MINT = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v'

// Helper: делает fetch с повторными попытками
async function fetchWithRetry(url, options = {}, retries = 2) {
  for (let i = 0; i <= retries; i++) {
    try {
      const res = await fetch(url, { timeout: 10000, ...options })
      if (res.ok) return res
      const text = await res.text()
      console.warn(`[fetch retry ${i}] ${res.status}: ${text.slice(0, 100)}`)
    } catch (err) {
      if (i === retries) throw err
      console.warn(`[fetch retry ${i}] ${err.message}`)
      await new Promise(r => setTimeout(r, 500))
    }
  }
  return null
}

// Получить цену токена через quote (1 USDC → token)
export async function getPrice(tokenMint) {
  if (tokenMint === USDC_MINT) return 1
  const q = await getQuote(USDC_MINT, tokenMint, '1000000') // 1 USDC
  if (!q || !q.outAmount) return null
  return Number(q.outAmount) / 1_000_000
}

// Цены для нескольких токенов (параллельно)
export async function getPrices(tokenMints) {
  const results = {}
  const batchSize = 3 // не больше 3 параллельных запросов к Jupiter

  for (let i = 0; i < tokenMints.length; i += batchSize) {
    const batch = tokenMints.slice(i, i + batchSize)
    const promises = batch.map(async mint => {
      const price = await getPrice(mint)
      if (price) {
        results[mint] = { price: price.toString(), id: mint }
      }
    })
    await Promise.all(promises)
  }

  return results
}

// Get quote for a swap via Jupiter quote API (swap/v1)
export async function getQuote(inputMint, outputMint, amount, slippageBps = CONFIG.SLIPPAGE_BPS) {
  const params = new URLSearchParams({
    inputMint,
    outputMint,
    amount: amount.toString(),
    slippageBps: slippageBps.toString(),
  })

  const url = `${CONFIG.JUPITER_API}/quote?${params}`
  const res = await fetchWithRetry(url)

  if (!res) {
    console.warn(`Jupiter quote failed (no response): ${url.slice(0, 80)}...`)
    return null
  }

  return await res.json()
}

// Get swap transaction (returns serialized tx)
export async function getSwapTransaction(quoteResponse, userPublicKey, options = {}) {
  const payload = {
    quoteResponse,
    userPublicKey,
    wrapAndUnwrapSol: true,
    dynamicComputeUnitLimit: true,
    prioritizationFeeLamports: options.priorityFee || 1000,
    ...options,
  }

  const res = await fetchWithRetry(`${CONFIG.JUPITER_API}/swap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!res) {
    console.warn('Jupiter swap failed (no response)')
    return null
  }

  return await res.json()
}
