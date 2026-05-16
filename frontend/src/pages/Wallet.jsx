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
        <p>💳 <strong>Кошельки</strong> — подключи Solana кошелёк для автоматической торговли.</p>
        <p>🔑 Private key в формате Base58 (экспорт из Phantom/Solflare).</p>
        <p>⚠️ Никогда не вводи ключ на подозрительных сайтах.</p>
      </div>

      <h2>🔗 Solana кошелёк</h2>
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
            <p className="muted" style={{ marginBottom: 12 }}>
              Кошелёк нужен для исполнения сделок
            </p>

            {!showInput ? (
              <button className="btn btn-primary" onClick={() => setShowInput(true)}>
                🔑 Подключить кошелёк
              </button>
            ) : (
              <div className="key-input-area">
                <textarea
                  className="key-input"
                  placeholder="Вставьте private key (Base58) из Phantom/Solflare..."
                  value={keyInput}
                  onChange={e => setKeyInput(e.target.value)}
                  rows={2}
                />
                <div className="key-actions">
                  <button className="btn btn-sm btn-primary" onClick={handleConnect} disabled={loading}>
                    {loading ? '⏳ Подключение...' : 'Подключить'}
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={() => setShowInput(false)}>
                    Отмена
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
