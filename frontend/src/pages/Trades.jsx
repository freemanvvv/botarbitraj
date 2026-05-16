import { useState, useEffect } from 'react'
import { useWebSocket } from '../hooks/useWebSocket'

const API = 'https://botarbitraj-1.onrender.com/api'

export default function Trades() {
  const { lastMessage } = useWebSocket()
  const [trades, setTrades] = useState([])

  useEffect(() => {
    if (lastMessage?.type === 'state') {
      setTrades(lastMessage.payload.trades || [])
    }
  }, [lastMessage])

  useEffect(() => {
    fetch(`${API}/trades`)
      .then(r => r.json())
      .then(data => setTrades(data || []))
      .catch(() => {})
  }, [])

  return (
    <div className="page">
      <div className="info-block">
        <p>📜 <strong>История сделок</strong> — все выполненные автоматические трейды.</p>
        <p>Здесь отображаются маршруты, профит и хэши транзакций в Solana.</p>
      </div>

      <h2>📜 История сделок</h2>

      {trades.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 40 }}>
          <p style={{ fontSize: 32, marginBottom: 8 }}>📭</p>
          <p className="muted">Сделок пока нет</p>
          <p className="muted" style={{ fontSize: 12, marginTop: 4 }}>
            Запусти бота — первая сделка появится при найденном профите
          </p>
        </div>
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
                {new Date(trade.timestamp).toLocaleString('ru-RU')}
              </div>
              <div className="trade-tx">
                {trade.result?.map((r, j) => (
                  <span key={j} style={{ background: '#0a0a1a', padding: '2px 6px', borderRadius: 4 }}>
                    Leg {r.leg}: {r.txId?.slice(0, 10)}...
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
