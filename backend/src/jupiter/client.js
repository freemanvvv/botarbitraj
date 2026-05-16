import fetch from 'node-fetch'
import { CONFIG } from '../config/index.js'

// Get price for a token via Jupiter price API
export async function getPrice(tokenMint) {
  const url = `${CONFIG.JUPITER_PRICE_API}?ids=${tokenMint}`
  const res = await fetch(url)
  const data = await res.json()
  return data.data?.[tokenMint]?.price || null
}

// Get prices for multiple tokens at once
export async function getPrices(tokenMints) {
  const ids = tokenMints.join(',')
  const url = `${CONFIG.JUPITER_PRICE_API}?ids=${ids}`
  const res = await fetch(url)
  const data = await res.json()
  return data.data || {}
}

// Get quote for a swap via Jupiter quote API
export async function getQuote(inputMint, outputMint, amount, slippageBps = CONFIG.SLIPPAGE_BPS) {
  const params = new URLSearchParams({
    inputMint,
    outputMint,
    amount: amount.toString(),
    slippageBps: slippageBps.toString(),
    onlyDirectRoutes: 'true',
  })

  const url = `${CONFIG.JUPITER_API}/quote?${params}`
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text()
    console.warn(`Jupiter quote error: ${res.status}`, text)
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

  const res = await fetch(`${CONFIG.JUPITER_API}/swap`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })

  if (!res.ok) {
    const text = await res.text()
    console.warn(`Jupiter swap error: ${res.status}`, text)
    return null
  }

  return await res.json()
}
