import { useState } from 'react'
import { useWallet } from '../hooks/useApi'

export default function Wallet() {
  const { wallet, loading, connect } = useWallet()
  const [keyInput, setKeyInput] = useState('')
  const [showInput, setShowInput] = useState(false)

  const handleConnect = async () => {
    if (!keyInput.trim()) return
    await connect(keyInput.trim())
    setKeyInput('')
    setShowInput(false)
  }

  return (
    <div className="page">
      <div className="info-block">
        <p>💳 <strong>Кошелёк Solana</strong> нужен для торговли.</p>
        <p>Если у тебя ещё нет кошелька — создай за 1 минуту.</p>
      </div>

      <div className="card" style={{ marginBottom: 8, textAlign: 'center', padding: 16 }}>
        <p style={{ fontSize: 14, marginBottom: 12, color: '#aaa' }}>Нет кошелька?</p>
        <div style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
          <a href="https://phantom.app/download" target="_blank" rel="noopener noreferrer"
             style={{ textDecoration: 'none' }}>
            <button className="btn btn-sm" style={{ background: '#ab9ff2', color: '#000', fontSize: 13 }}>
              👻 Phantom
            </button>
          </a>
          <a href="https://solflare.com/download" target="_blank" rel="noopener noreferrer"
             style={{ textDecoration: 'none' }}>
            <button className="btn btn-sm" style={{ background: '#fc7b3a', color: '#fff', fontSize: 13 }}>
              🦊 Solflare
            </button>
          </a>
          <a href="https://backpack.app/download" target="_blank" rel="noopener noreferrer"
             style={{ textDecoration: 'none' }}>
            <button className="btn btn-sm" style={{ background: '#1e1e2e', color: '#fff', border: '1px solid #333', fontSize: 13 }}>
              🎒 Backpack
            </button>
          </a>
        </div>
        <p style={{ fontSize: 11, color: '#555', marginTop: 10 }}>
          Скачай расширение или приложение → создай кошелёк → сохрани seed-фразу и private key
        </p>
      </div>

      <h2>🔗 Подключить кошелёк</h2>
      <div className="wallet-card">
        {wallet?.connected ? (
          <>
            <div className="wallet-status connected">🟢 Подключён</div>
            <div className="wallet-address">
              <span className="label">Адрес:</span>
              <code>{wallet.address.slice(0, 6)}...{wallet.address.slice(-4)}</code>
            </div>
            {wallet.balanceSol !== undefined && (
              <div className="wallet-balance">
                <span className="label">Баланс:</span>
                <span className="value">{wallet.balanceSol.toFixed(4)} SOL</span>
              </div>
            )}
          </>
        ) : (
          <>
            <div className="wallet-status disconnected">🔴 Не подключён</div>
            <p className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
              Вставь private key из кошелька
            </p>

            {!showInput ? (
              <button className="btn btn-primary" onClick={() => setShowInput(true)}>
                🔑 Ввести ключ
              </button>
            ) : (
              <div className="key-input-area">
                <textarea
                  className="key-input"
                  placeholder="Private key (Base58) — экспорт из кошелька..."
                  value={keyInput}
                  onChange={e => setKeyInput(e.target.value)}
                  rows={2}
                />
                <div className="key-actions">
                  <button className="btn btn-sm btn-primary" onClick={handleConnect} disabled={loading}>
                    {loading ? '⏳' : 'Подключить'}
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setShowInput(false)}>
                    Назад
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
