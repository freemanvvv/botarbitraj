import { useState, useEffect } from 'react'
import toast from 'react-hot-toast'

const BASE = import.meta.env.VITE_API_URL || '/api'

export default function Spreads() {
  const [spreads, setSpreads] = useState([])
  const [loading, setLoading] = useState(false)
  const [keys, setKeys] = useState({ apiKey: '', apiSecret: '' })
  const [configuring, setConfiguring] = useState(false)
  const [configured, setConfigured] = useState(false)

  const fetchSpreads = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${BASE}/binance/spreads`)
      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: '?' }))
        throw new Error(err.error || 'Не настроены Binance ключи')
      }
      const data = await res.json()
      setSpreads(data)
    } catch (err) {
      toast.error(err.message)
      setSpreads([])
    }
    setLoading(false)
  }

  const checkStatus = async () => {
    try {
      const res = await fetch(`${BASE}/binance/status`)
      const data = await res.json()
      setConfigured(data.configured)
    } catch { /* ignore */ }
  }

  const configureKeys = async () => {
    if (!keys.apiKey || !keys.apiSecret) {
      toast.error('Введи API Key и Secret')
      return
    }
    setConfiguring(true)
    try {
      const res = await fetch(`${BASE}/binance/configure`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(keys),
      })
      if (!res.ok) throw new Error('Ошибка конфигурации')
      toast.success('✅ Binance ключи сохранены')
      setConfigured(true)
      setKeys({ apiKey: '', apiSecret: '' })
    } catch (err) {
      toast.error(err.message)
    }
    setConfiguring(false)
  }

  useEffect(() => { checkStatus() }, [])

  return (
    <div className="page">
      {/* ОБЪЯСНЕНИЕ */}
      <div className="info-block">
        <p><strong>⚡ CEX-DEX спреды</strong> — это разница цен между биржами.</p>
        <p style={{ fontSize: 13, color: '#999', marginTop: 6 }}>
          Мы сравниваем цены Jupiter (DEX, Solana) с ценами Binance (CEX).
          Если спред больше нуля — на одной бирже дешевле, чем на другой.
          Это потенциальная возможность для кросс-биржевого арбитража.
        </p>
      </div>

      <h2>🔑 Настройка Binance</h2>
      {configured ? (
        <div className="card" style={{ textAlign: 'center' }}>
          <p style={{ color: '#4ade80' }}>✅ Binance API подключён</p>
        </div>
      ) : (
        <div className="card">
          <p className="muted" style={{ fontSize: 12, marginBottom: 8 }}>
            Введи API ключи от Binance (Read-only, без вывода средств).
            <br />Создаются в Binance → API Management.
          </p>
          <input
            className="input"
            placeholder="API Key"
            value={keys.apiKey}
            onChange={e => setKeys({ ...keys, apiKey: e.target.value })}
          />
          <input
            className="input"
            type="password"
            placeholder="API Secret"
            value={keys.apiSecret}
            onChange={e => setKeys({ ...keys, apiSecret: e.target.value })}
          />
          <button className="btn btn-primary" onClick={configureKeys} disabled={configuring} style={{ width: '100%' }}>
            {configuring ? '⏳ Сохраняю...' : 'Сохранить ключи'}
          </button>
        </div>
      )}

      <h2>📊 Спреды CEX/DEX</h2>
      <p className="section-desc">
        Таблица показывает цены на Jupiter (DEX) и Binance (CEX), а также разницу в %.
      </p>
      <div style={{ marginBottom: 12 }}>
        <button className="btn btn-primary" onClick={fetchSpreads} disabled={loading} style={{ width: '100%' }}>
          {loading ? '⏳ Загрузка...' : '📊 Показать спреды'}
        </button>
      </div>

      {spreads.length > 0 && (
        <table className="spreads-table">
          <thead>
            <tr>
              <th>Пара</th>
              <th>DEX (Jupiter)</th>
              <th>CEX (Binance)</th>
              <th>Спред</th>
            </tr>
          </thead>
          <tbody>
            {spreads.map((s, i) => (
              <tr key={i}>
                <td>{s.symbol}</td>
                <td>${typeof s.dexPrice === 'number' ? s.dexPrice.toFixed(4) : '—'}</td>
                <td>${typeof s.cexPrice === 'number' ? s.cexPrice.toFixed(4) : '—'}</td>
                <td style={{ color: (s.spreadPercent || 0) > 0 ? '#4ade80' : '#f87171' }}>
                  {s.spreadPercent != null ? `${s.spreadPercent.toFixed(2)}%` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {spreads.length === 0 && !loading && (
        <div className="card" style={{ textAlign: 'center', padding: 30 }}>
          <p className="muted">
            {configured ? '📭 Нажми "Показать спреды"' : '🔑 Сначала подключи Binance API'}
          </p>
        </div>
      )}
    </div>
  )
}
