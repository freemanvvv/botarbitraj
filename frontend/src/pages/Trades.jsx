import { useState, useEffect } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'

export default function Trades() {
  const { lastMessage } = useWebSocket()
  const [trades, setTrades] = useState([])

  useEffect(() => {
    if (lastMessage?.type === 'state') {
      setTrades(lastMessage.payload.trades || [])
    }
  }, [lastMessage])

  // Also fetch on mount
  useEffect(() => {
    fetch('/api/trades')
      .then(r => r.json())
      .then(data => setTrades(data || []))
      .catch(() => {})
  }, [])

  return (
    <div className="page">
      <h2>📜 Trade History</h2>

      {trades.length === 0 ? (
        <p className="muted">No trades yet. Start the bot to see results.</p>
      ) : (
        <div className="trade-list">
          {[...trades].reverse().map((trade, i) => (
            <div key={i} className="trade-card">
              <div className="trade-header">
                <span className="trade-route">{trade.route}</span>
                <span className={`trade-profit ${trade.profitBps >= 0 ? 'positive' : 'negative'}`}>
                  +{trade.profitPercent}%
                </span>
              </div>
              <div className="trade-time">
                {new Date(trade.timestamp).toLocaleString()}
              </div>
              <div className="trade-tx">
                {trade.result?.map((r, j) => (
                  <span key={j} className="tx-link">
                    Leg {r.leg}: {r.txId?.slice(0, 12)}...
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
