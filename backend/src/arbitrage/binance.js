import fetch from 'node-fetch'
import crypto from 'crypto'

let apiKey = null
let secretKey = null

export function configure(api, secret) {
  apiKey = api
  secretKey = secret
}

export function isConfigured() {
  return !!apiKey
}

// Get prices from Binance for given symbols
export async function getPrices(symbols = ['SOLUSDT', 'RAYUSDT', 'JUPUSDT', 'BONKUSDT', 'USDCUSDT']) {
  try {
    const ids = symbols.join(',')
    const url = `https://api.binance.com/api/v3/ticker/price?symbols=[${symbols.map(s => `"${s}"`).join(',')}]`
    const res = await fetch(url)
    if (!res.ok) return {}
    const data = await res.json()
    const result = {}
    for (const item of data) {
      result[item.symbol] = parseFloat(item.price)
    }
    return result
  } catch (err) {
    console.warn('Binance price fetch error:', err.message)
    return {}
  }
}

// Get order book for spread analysis
export async function getOrderBook(symbol, limit = 10) {
  const url = `https://api.binance.com/api/v3/depth?symbol=${symbol}&limit=${limit}`
  const res = await fetch(url)
  if (!res.ok) return null
  return await res.json()
}

// Get account balance (requires auth)
export async function getBalance() {
  if (!apiKey || !secretKey) throw new Error('Binance API not configured')

  const timestamp = Date.now()
  const queryString = `timestamp=${timestamp}`
  const signature = crypto
    .createHmac('sha256', secretKey)
    .update(queryString)
    .digest('hex')

  const res = await fetch(`https://api.binance.com/api/v3/account?${queryString}&signature=${signature}`, {
    headers: { 'X-MBX-APIKEY': apiKey },
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Binance account error: ${text}`)
  }

  return await res.json()
}

// Compare Binance price vs Jupiter price
export function calcSpread(binancePrice, jupiterPrice) {
  if (!binancePrice || !jupiterPrice) return null
  const diff = ((jupiterPrice - binancePrice) / binancePrice) * 10000 // in bps
  return {
    binance: binancePrice,
    jupiter: jupiterPrice,
    spreadBps: Math.round(diff),
    spreadPercent: (diff / 100).toFixed(2),
    direction: diff > 0 ? 'buy_binance_sell_jupiter' : 'buy_jupiter_sell_binance',
  }
}
