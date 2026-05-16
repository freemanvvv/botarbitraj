import { useState } from 'react'
import { useScan } from '../hooks/useApi'

export default function Scanner() {
  const { results, loading, scan } = useScan()
  const [expanded, setExpanded] = useState(null)
  const all = results.all || []
  const profitable = results.profitable || []

  return (
    <div className="page">
      {/* ОБЪЯСНЕНИЕ */}
      <div className="info-block">
        <p><strong>🔍 Треугольный арбитраж</strong> — это когда цена одного и того же токена отличается на разных парах.</p>
        <p style={{ fontSize: 13, color: '#999', marginTop: 6 }}>
          Пример: 1 USDC → SOL → RAY → USDC. Если после всех трёх обменов у тебя больше USDC чем было — это арбитраж.
          Бот проверяет десятки таких треугольников за один скан.
        </p>
      </div>

      <h2>🔎 Сканирование</h2>
      <div className="card" style={{ marginBottom: 12, textAlign: 'center' }}>
        <p className="muted" style={{ fontSize: 13, marginBottom: 8 }}>
          {all.length > 0
            ? `Последний скан: ${all.length} треугольников, ${profitable.length} прибыльных`
            : 'Нажми кнопку для поиска арбитражных возможностей'}
        </p>
        <button className="btn btn-primary" onClick={scan} disabled={loading}>
          {loading ? '⏳ Сканирую...' : '🔍 Запустить скан'}
        </button>
      </div>

      {/* ПРИБЫЛЬНЫЕ */}
      {profitable.length > 0 && (
        <>
          <h2>🔥 Прибыльные треугольники</h2>
          <p className="section-desc">Найдены арбитражные возможности с положительной прибылью.</p>
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
                {opp.profitBps > 0 && (
                  <div className="opp-detail">
                    {Number(opp.amountIn).toFixed(2)} USDC → {Number(opp.amountOut).toFixed(2)} USDC
                  </div>
                )}
                {expanded === i && (
                  <div className="expanded-detail">
                    <p>Профит: <strong>{opp.profitBps} bps</strong> ({opp.profitPercent}%)</p>
                    <p>Старт: {opp.amountIn} USDC → Финиш: {Number(opp.amountOut).toFixed(6)} USDC</p>
                    <p>Маршрут: {opp.route}</p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}

      {/* ВСЕ ТРЕУГОЛЬНИКИ */}
      {all.length > 0 && (
        <>
          <h2>📊 Результаты скана</h2>
          <p className="section-desc">Все проверенные треугольники, отсортированы по профиту.</p>
          <div className="list">
            {all.map((opp, i) => (
              <div className="list-item" key={i}>
                <div className="opp-header">
                  <span className="opp-route" style={{ fontSize: 12 }}>{opp.route || opp.triangle?.join(' → ')}</span>
                  <span className={`profit-badge ${opp.profitBps >= 0 ? 'positive' : 'negative'}`}>
                    {opp.profitPercent || '0.00'}%
                  </span>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {all.length === 0 && !loading && (
        <div className="card" style={{ textAlign: 'center', padding: 40 }}>
          <p className="muted">📭 Пока нет данных</p>
          <p style={{ fontSize: 12, color: '#555', marginTop: 8 }}>
            Запусти сканирование — бот проверит комбинации токенов
          </p>
        </div>
      )}
    </div>
  )
}
