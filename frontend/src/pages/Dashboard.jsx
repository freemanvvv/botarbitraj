import { usePrices, useScan } from '../hooks/useApi'
import { AreaChart, Area, ResponsiveContainer, Tooltip } from 'recharts'

// Fake sparklines for demo
const generateSpark = (base, n = 24) =>
  Array.from({ length: n }, (_, i) => ({
    t: i,
    v: base + Math.sin(i * 0.5) * base * 0.02 + (Math.random() - 0.5) * base * 0.01,
  }))

export default function Dashboard() {
  const { prices, loading } = usePrices()
  const { results, loading: scanning, scan } = useScan()

  const tokens = [
    { sym: 'SOL', name: 'Solana', addr: 'So11111111111111111111111111111111111111112' },
    { sym: 'RAY', name: 'Raydium', addr: '4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R' },
    { sym: 'JUP', name: 'Jupiter', addr: 'JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN' },
    { sym: 'BONK', name: 'Bonk', addr: 'DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263' },
  ]

  return (
    <div className="page">
      {/* ПРИВЕТСТВИЕ */}
      <div className="info-block">
        <p>🚀 <strong>SolArb</strong> — бот для поиска треугольного арбитража на Solana через Jupiter.</p>
        <p style={{ fontSize: 13, color: '#999', marginTop: 6 }}>
          Анализируем цены на DEX, ищем неэффективности, исполняем сделки в 3 ноги за один цикл.
        </p>
      </div>

      {/* ТЕКУЩИЕ ЦЕНЫ */}
      <h2>💹 Текущие цены</h2>
      <p className="section-desc">Актуальные курсы топ-токенов Solana относительно USDC.</p>
      <div className="grid-2">
        {tokens.map(({ sym, name, addr }) => (
          <div className="card" key={sym}>
            <div className="token-info">
              <span className="token-symbol">{sym}</span>
              <span className="token-name">{name}</span>
            </div>
            <div className="price-value">
              {prices?.[addr] ? `$${Number(prices[addr].price).toFixed(4)}` : loading ? '...' : '—'}
            </div>
            <ResponsiveContainer width="100%" height={40}>
              <AreaChart data={generateSpark(prices?.[addr] ? Number(prices[addr].price) : 100)}>
                <defs>
                  <linearGradient id={`grad-${sym}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#7c5cbf" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#7c5cbf" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Tooltip />
                <Area type="monotone" dataKey="v" stroke="#7c5cbf" fill={`url(#grad-${sym})`} strokeWidth={1.5} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        ))}
      </div>

      {/* СКАНИРОВАНИЕ */}
      <h2>🔍 Последний скан</h2>
      <p className="section-desc">
        Сканер проверяет комбинации токенов в поисках треугольного арбитража:
        покупает дешёвый токен A → меняет на B → продаёт обратно в USDC.
        Если разница цен положительная — найдена возможность.
      </p>
      <div className="card scan-summary">
        {results.all?.length > 0 ? (
          <>
            <div className="scan-stats">
              <span>Проверено треугольников: <strong>{results.all.length}</strong></span>
              <span>Прибыльных: <strong>{results.profitable?.length || 0}</strong></span>
            </div>
            {results.profitable?.length > 0 && (
              <div className="best-opp">
                Лучший: <span className="profit-badge">{results.profitable[0].profitPercent}%</span>
                <span className="route-text">{results.profitable[0].route}</span>
              </div>
            )}
          </>
        ) : (
          <p className="muted" style={{ textAlign: 'center' }}>
            {scanning ? '⏳ Сканирование...' : '📭 Нажми "Сканировать", чтобы начать'}
          </p>
        )}
        <button className="btn btn-primary" onClick={scan} disabled={scanning} style={{ marginTop: 12, width: '100%' }}>
          {scanning ? '⏳ Сканирую...' : '🔍 Сканировать'}
        </button>
      </div>
    </div>
  )
}
