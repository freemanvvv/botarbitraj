import { useState, useEffect } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'
import { usePrices } from '../hooks/useApi'
import { AreaChart, Area, ResponsiveContainer } from 'recharts'

export default function Dashboard() {
  const { connected } = useWebSocket()
  const { prices, loading } = usePrices()
  const [history, setHistory] = useState([])

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
        <span>{connected ? 'Подключено к серверу' : 'Переподключение...'}</span>
      </div>

      <div className="info-block">
        <p>⚡ <strong>SolArb</strong> — бот для треугольного арбитража на Solana.</p>
        <p>Сканирует цены через <strong>Jupiter</strong>, ищет разнонаправленные спреды в треугольниках USDC → SOL → JUP → USDC и выполняет сделки.</p>
      </div>

      <h2>📊 Курсы токенов</h2>
      {loading ? (
        <p className="muted">Загрузка...</p>
      ) : (
        <div className="token-grid">
          {tokens.map(([symbol, data]) => {
            const price = data.price ? parseFloat(data.price) : 0
            const prevPrice = history.length > 1 ? history[history.length - 2]?.[symbol] : price
            const change = prevPrice ? ((price - prevPrice) / prevPrice * 100).toFixed(2) : '—'

            return (
              <div key={symbol} className="token-card">
                <div className="token-header">
                  <span className="token-symbol">{symbol}</span>
                  <span className={`token-change ${change !== '—' && parseFloat(change) >= 0 ? 'up' : 'down'}`}>
                    {change !== '—' ? `${change}%` : '—'}
                  </span>
                </div>
                <div className="token-price">
                  ${price ? price.toFixed(price < 1 ? 6 : 4) : '—'}
                </div>
                <div className="token-sub">
                  Jupiter DEX <span className="tag tag-dex">DEX</span>
                </div>
                <div className="sparkline">
                  <ResponsiveContainer width="100%" height={45}>
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
                        isAnimationActive={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
