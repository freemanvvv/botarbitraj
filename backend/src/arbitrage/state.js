// In-memory state + WebSocket broadcast
let scanResults = []
let opportunities = []
let botStatus = 'idle' // idle | running
let trades = []
let walletConnected = false
let walletAddress = null
let wssClients = []

export const state = {
  getScanResults: () => scanResults,
  setScanResults: (r) => { scanResults = r; broadcast() },

  getOpportunities: () => opportunities,
  setOpportunities: (o) => { opportunities = o; broadcast() },

  getBotStatus: () => botStatus,
  setBotStatus: (s) => { botStatus = s; broadcast() },

  getTrades: () => trades,
  addTrade: (opportunity, result) => {
    trades.push({
      timestamp: Date.now(),
      route: opportunity.route,
      profitBps: opportunity.profitBps,
      profitPercent: opportunity.profitPercent,
      result,
    })
    // Keep last 100 trades
    if (trades.length > 100) trades = trades.slice(-100)
    broadcast()
  },

  getWalletConnected: () => walletConnected,
  setWalletConnected: (v, addr) => { walletConnected = v; walletAddress = addr; broadcast() },

  getWalletAddress: () => walletAddress,

  getFullState: () => ({
    scanResults,
    opportunities,
    botStatus,
    trades: trades.slice(-20),
    walletConnected,
    walletAddress,
  }),
}

// WebSocket
export function setWssClients(clients) {
  wssClients = clients
}

function broadcast() {
  const data = JSON.stringify({ type: 'state', payload: state.getFullState() })
  for (const ws of wssClients) {
    try { ws.send(data) } catch { /* ignore */ }
  }
}
