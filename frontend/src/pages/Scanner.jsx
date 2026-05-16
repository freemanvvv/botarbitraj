import { useState, useEffect, useRef } from 'react'
import toast from 'react-hot-toast'

const API = 'https://botarbitraj-1.onrender.com/api'

export default function Scanner() {
  const [results, setResults] = useState({ all: [], profitable: [] })
  const [loading, setLoading] = useState(false)
  const [autoMode, setAutoMode] = useState(false)
  const [expanded, setExpanded] = useState(null)
  const autoRef = useRef(null)
  const all = results.all || []
  const profitable = results.profitable || []

  const doScan = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/scan`, { timeout: 20000 })
      const data = await res.json()
      setResults(data)
      if (data.profitable?.length > 0) {
        toast.success(`🔥 ${data.profitable.length} возможностей!`, { duration: 4000 })
      }
    } catch (err) {
      if (autoMode) return
      toast.error(err.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    if (autoMode) {
      doScan()
      autoRef.current = setInterval(doScan, 5000)
    } else {
      clearInterval(autoRef.current)
    }
    return () => clearInterval(autoRef.current)
  }, [autoMode])

  const toggleAuto = () => setAutoMode(!autoMode)

  return (
    <div className="page">
      <div className="info-block">
        <p><strong>🔍 Треугольный арбитраж</strong> — это когда цена одного и того же токена отличается на разных парах.</p>
        <p style={{ fontSize: 13, color: '#999', marginTop: 6 }}>
          Пример: USDC → SOL → RAY → USDC. Если после трёх обменов USDC стало больше — это арбитраж.
        </p>
      </div>

      <h2>🎛 Управление сканом</h2>
      <div className="card" style={{ marginBottom: 12 }}>
        {all.length > 0 && (
          <div className="scan-summary" style={{ marginBottom: 10, textAlign: 'center' }}>
            <span style={{ fontSize: 13, color: '#888' }}>
              Проверено: <strong>{all.length}</strong> · 
              Прибыльных: <strong>{profitable.length}</strong>
            </span>
          </div>
        )}
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-primary" onClick={doScan} disabled={loading} style={{ flex: 1 }}>
            {loading ? '⏳ Скан...' : '🔍 Сканировать'}
          </button>
          <button
            className={`btn ${autoMode ? 'btn-danger' : 'btn-ghost'}`}
            onClick={toggleAuto}
            style={{ flex: 1 }}
          >
            {autoMode ? '⏹ Стоп' : '🔄 Авто'}
          </button>
        </div>
        {autoMode && (
          <p style={{ fontSize: 11, color: '#00ff88', textAlign: 'center', marginTop: 8 }}>
            🔄 Авто-скан каждые 5с
          </p>
        )}
      </div>

      {profitable.length > 0 && (
        <>
          <h2>🔥 Прибыльные треугольники</h2>
          <div className="list">
            {profitable.map((opp, i) => (
              <div
                className={`list-item opp-card ${expanded === i ? 'expanded' : ''}`}
                key={i}
                onClick={() => setExpanded(expanded === i ? null : i)}
              >
                <div className="opp-header">
                  <span className="opp-route">{opp.route}</span>
                  <span className={`profit-badge ${opp.profitBps > 0 ? 'positive' : 'negative'}`}>
                    {opp.profitPercent}%
                  </span>
                </div>
                <div className="opp-detail">
                  100 USDC → {opp.amountOut ? (opp.amountOut * (100 / opp.amountIn)).toFixed(2) : '?'} USDC
                </div>
                {expanded === i && (
                  <div className="expanded-detail">
                    <p>Профит: <strong>{opp.profitBps} bps</strong> ({opp.profitPercent}%)</p>
                    <p>Вход: 100 USDC → Выход: {opp.amountOut ? (opp.amountOut * (100 / opp.amountIn)).toFixed(4) : '?'} USDC</p>
                    <p>Маршрут: {opp.route}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {all.length > 0 && (
        <>
          <h2>📊 Результаты</h2>
          <div className="list">
            {all.map((opp, i) => (
              <div className="list-item" key={i}>
                <div className="opp-header">
                  <span className="opp-route" style={{ fontSize: 12 }}>{opp.route}</span>
                  <span className={`profit-badge ${opp.profitBps >= 0 ? 'positive' : 'negative'}`}>
                    {opp.profitPercent || '0.00'}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {all.length === 0 && !loading && !autoMode && (
        <div className="card" style={{ textAlign: 'center', padding: 40 }}>
          <p className="muted">📭 Нет данных</p>
        </div>
      )}
      {all.length === 0 && autoMode && (
        <div className="card" style={{ textAlign: 'center', padding: 30 }}>
          <p className="muted" style={{ color: '#ffcc00' }}>
            🔄 Авто-скан активен, ждём арбитраж...
          </p>
        </div>
      )}
    </div>
  )
}
