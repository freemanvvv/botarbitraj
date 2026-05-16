import { useEffect } from 'react'
import { useScan } from '../hooks/useApi'

export default function Scanner() {
  const { results, loading, scan } = useScan()

  useEffect(() => {
    scan()
    const timer = setInterval(scan, 10000)
    return () => clearInterval(timer)
  }, [])

  const profitable = results.profitable || []
  const all = results.all || []

  return (
    <div className="page">
      <div className="info-block">
        <p>🔍 <strong>Треугольный сканер</strong> проверяет цепочки обменов USDC → ТокенA → ТокенB → USDC.</p>
        <p>Профит считается в <strong>bps</strong> (1% = 100 bps). Минимальный порог: 30 bps (0.3%).</p>
      </div>

      <div className="section-header">
        <h3>Найдено маршрутов: {all.length}</h3>
        <button className="btn btn-sm" onClick={scan} disabled={loading}>
          {loading ? '⏳' : '🔄 Сканировать'}
        </button>
      </div>

      {profitable.length > 0 && (
        <>
          <div className="section-header" style={{ marginTop: 8 }}>
            <h3>🔥 Прибыльные ({profitable.length})</h3>
          </div>
          <div className="opp-list">
            {profitable.map((opp, i) => (
              <div key={i} className="opp-card profitable">
                <div className="opp-route">{opp.route}</div>
                <div className="opp-profit positive">+{opp.profitPercent}%</div>
                <div className="opp-detail">{opp.profitBps} bps · ${opp.amountOut.toFixed(2)} USDC</div>
              </div>
            ))}
          </div>
          <div className="section-divider" />
        </>
      )}

      <div className="section-header" style={{ marginTop: 8 }}>
        <h3>Все маршруты</h3>
      </div>
      <div className="opp-list">
        {all.map((opp, i) => (
          <div key={i} className={`opp-card ${opp.profitBps >= 30 ? 'profitable' : 'neutral'}`}>
            <div className="opp-route">{opp.route}</div>
            <div className={`opp-profit ${opp.profitBps >= 0 ? 'positive' : 'negative'}`}>
              {opp.profitBps >= 0 ? '+' : ''}{opp.profitPercent}%
            </div>
            <div className="opp-detail">
              {opp.profitBps} bps · {opp.profitBps >= 30 ? '🔥 прибыльный' : '❌ невыгодно'}
            </div>
          </div>
        ))}
        {all.length === 0 && !loading && (
          <p className="muted">Нет данных. Нажми «Сканировать».</p>
        )}
      </div>
    </div>
  )
}
