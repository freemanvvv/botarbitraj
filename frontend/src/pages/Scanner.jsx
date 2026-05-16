import { useEffect } from 'react'
import { useScan, useApi } from '../hooks/useApi'

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
      <div className="flex-between">
        <h2>Triangle Scanner</h2>
        <button className="btn btn-sm" onClick={scan} disabled={loading}>
          {loading ? '⏳' : '🔄 Scan'}
        </button>
      </div>

      {profitable.length > 0 && (
        <>
          <h3 className="profit-title">🔥 Opportunities</h3>
          <div className="opp-list">
            {profitable.map((opp, i) => (
              <div key={i} className="opp-card profitable">
                <div className="opp-route">{opp.route}</div>
                <div className="opp-profit">+{opp.profitPercent}%</div>
                <div className="opp-detail">{opp.profitBps} bps · ${opp.amountOut.toFixed(2)}</div>
              </div>
            ))}
          </div>
        </>
      )}

      <h3 className={profitable.length > 0 ? 'sub-heading' : ''}>All Routes</h3>
      <div className="opp-list">
        {all.map((opp, i) => (
          <div key={i} className={`opp-card ${opp.profitBps >= 30 ? 'profitable' : 'neutral'}`}>
            <div className="opp-route">{opp.route}</div>
            <div className={`opp-profit ${opp.profitBps >= 0 ? 'positive' : 'negative'}`}>
              {opp.profitBps >= 0 ? '+' : ''}{opp.profitPercent}%
            </div>
            <div className="opp-detail">{opp.profitBps} bps</div>
          </div>
        ))}
        {all.length === 0 && !loading && (
          <p className="muted">No data. Run a scan.</p>
        )}
      </div>
    </div>
  )
}
