import { useState, useEffect } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import { usePrices } from '../hooks/useApi'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Area, AreaChart } from 'recharts'

export default function Dashboard() {
  const { connected } = useWebSocket()
  const { prices, loading } = usePrices()
  const [history, setHistory] = useState([])

  // Build price history for sparklines
  useEffect(() => {
    if (!prices || Object.keys(prices).length === 0) return

    const entry = { time: new Date().toLocaleTimeString() }
    for (const [symbol, data] of Object.entries(prices)) {
      entry[symbol] = data.price ? parseFloat(data.price) : 0
    }
    setHistory(prev => [...prev.slice(-30), entry])
  }, [prices])

  const tokens = Object.entries(prices || {})

  return (
    <div className="page">
      <div className="status-bar">
        <span className={`indicator ${connected ? 'online' : 'offline'}`} />
        <span>{connected ? 'Connected' : 'Reconnecting...'}</span>
      </div>

      <h2>Token Prices</h2>
      {loading ? (
        <p className="muted">Loading...</p>
      ) : (
        <div className="token-grid">
          {tokens.map(([symbol, data]) => (
            <div key={symbol} className="token-card">
              <div className="token-symbol">{symbol}</div>
              <div className="token-price">
                ${data.price ? parseFloat(data.price).toFixed(6) : '—'}
              </div>
              <div className="sparkline">
                <ResponsiveContainer width="100%" height={50}>
                  <AreaChart data={history}>
                    <defs>
                      <linearGradient id={`grad-${symbol}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#00ff88" stopOpacity={0.3} />
                        <stop offset="100%" stopColor="#00ff88" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <Area
                      type="monotone"
                      dataKey={symbol}
                      stroke="#00ff88"
                      fill={`url(#grad-${symbol})`}
                      strokeWidth={1.5}
                      dot={false}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
