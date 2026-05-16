import { useState, useEffect, useCallback } from 'react'
import { useApi } from '../hooks/useApi'

const API = import.meta.env.VITE_API_URL || '/api'

export default function Spreads() {
  const { get, post, loading } = useApi()
  const [spreads, setSpreads] = useState(null)
  const [binanceKey, setBinanceKey] = useState('')
  const [binanceSecret, setBinanceSecret] = useState('')
  const [configured, setConfigured] = useState(false)
  const [showConfig, setShowConfig] = useState(false)

  const fetchSpreads = useCallback(async () => {
    const data = await get('/binance/spreads')
    setSpreads(data)
  }, [get])

  const checkStatus = useCallback(async () => {
    try {
      const data = await get('/binance/status')
      setConfigured(data.configured)
      if (data.configured) fetchSpreads()
    } catch { /* ignore */ }
  }, [get, fetchSpreads])

  useEffect(() => {
    checkStatus()
    const timer = setInterval(checkStatus, 15000)
    return () => clearInterval(timer)
  }, [])

  const handleConfigure = async () => {
    if (!binanceKey.trim() || !binanceSecret.trim()) return
    await post('/binance/configure', { apiKey: binanceKey.trim(), secretKey: binanceSecret.trim() })
    setBinanceKey('')
    setBinanceSecret('')
    setShowConfig(false)
    await checkStatus()
  }

  return (
    <div className="page">
      <div className="info-block">
        <p>⚡ <strong>CEX ↔ DEX спреды</strong> — сравнивает цены Binance (CEX) и Jupiter (Solana DEX).</p>
        <p>Положительный спред = на Binance дешевле. Можно купить на CEX, перевести и продать на DEX.</p>
      </div>

      <div className="binance-section">
        <div className="binance-header">
          <span style={{ fontSize: 20 }}>🟡</span>
          <h3>Binance CEX</h3>
          {configured && <span className="tag tag-sol">Подключено</span>}
        </div>

        {!configured && !showConfig && (
          <div className="card" style={{ textAlign: 'center' }}>
            <p className="muted" style={{ marginBottom: 12 }}>Binance API ключи не настроены</p>
            <button className="btn btn-gold btn-sm" onClick={() => setShowConfig(true)}>
              🔑 Подключить Binance
            </button>
          </div>
        )}

        {showConfig && (
          <div className="card">
            <p style={{ fontSize: 12, color: '#888', marginBottom: 12 }}>
              API ключ создаётся в Binance → API Management (только чтение, без вывода)
            </p>
            <input
              className="key-input"
              placeholder="API Key"
              value={binanceKey}
              onChange={e => setBinanceKey(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <input
              className="key-input"
              placeholder="Secret Key"
              value={binanceSecret}
              onChange={e => setBinanceSecret(e.target.value)}
              type="password"
              style={{ marginBottom: 8 }}
            />
            <div className="key-actions">
              <button className="btn btn-sm btn-gold" onClick={handleConfigure} disabled={loading}>
                {loading ? '⏳' : 'Подключить'}
              </button>
              <button className="btn btn-sm btn-ghost" onClick={() => setShowConfig(false)}>
                Отмена
              </button>
            </div>
          </div>
        )}

        {configured && (
          <>
            <div className="section-header">
              <h3>Спреды Binance → Jupiter</h3>
              <button className="btn btn-sm btn-ghost" onClick={fetchSpreads} disabled={loading}>
                🔄
              </button>
            </div>

            {spreads ? (
              Object.entries(spreads).map(([token, data]) => (
                <div key={token} className="binance-card">
                  <span className="binance-tag">CEX → DEX</span>
                  <div className="spread-row">
                    <span className="spread-token">{token}</span>
                    <div className="spread-prices">
                      <div>
                        Binance: <strong>${data.binance?.toFixed(4)}</strong>
                      </div>
                      <div>
                        Jupiter: <strong>${data.jupiter?.toFixed(4)}</strong>
                      </div>
                    </div>
                  </div>
                  <div className="spread-row" style={{ borderBottom: 'none' }}>
                    <span style={{ fontSize: 12, color: '#888' }}>Спред</span>
                    <span className={`spread-value ${data.spreadBps > 0 ? 'spread-positive' : 'spread-negative'}`}>
                      {data.spreadBps > 0 ? '+' : ''}{data.spreadBps} bps
                    </span>
                  </div>
                </div>
              ))
            ) : (
              <p className="muted">Загрузка...</p>
            )}
          </>
        )}
      </div>

      <div className="info-block" style={{ marginTop: 16 }}>
        <p>💡 <strong>Совет:</strong> Положительный спред {`>`} 50 bps уже может быть интересен, но учитывай:
        комиссию Binance (0.1%), газ Solana (~0.00001 SOL), и время перевода между CEX и DEX.</p>
      </div>
    </div>
  )
}
