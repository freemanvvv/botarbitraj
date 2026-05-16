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
      <h2>💳 Wallet</h2>

      {wallet?.connected ? (
        <div className="wallet-card">
          <div className="wallet-status connected">🟢 Connected</div>
          <div className="wallet-address">
            <span className="label">Address:</span>
            <code>{wallet.address.slice(0, 8)}...{wallet.address.slice(-6)}</code>
          </div>
          {wallet.balanceSol !== undefined && (
            <div className="wallet-balance">
              <span className="label">Balance:</span>
              <span className="value">{wallet.balanceSol.toFixed(4)} SOL</span>
            </div>
          )}
          <div className="wallet-actions">
            <button className="btn btn-sm" onClick={() => {}}>🔁 Refresh</button>
          </div>
        </div>
      ) : (
        <div className="wallet-card">
          <div className="wallet-status disconnected">🔴 Not Connected</div>
          <p className="muted">Connect your Solana wallet to start trading</p>

          {!showInput ? (
            <button className="btn" onClick={() => setShowInput(true)}>
              🔑 Connect Wallet
            </button>
          ) : (
            <div className="key-input-area">
              <textarea
                className="key-input"
                placeholder="Paste your private key (base58 or JSON array)..."
                value={keyInput}
                onChange={e => setKeyInput(e.target.value)}
                rows={3}
              />
              <div className="key-actions">
                <button className="btn" onClick={handleConnect} disabled={loading}>
                  {loading ? '⏳ Connecting...' : 'Connect'}
                </button>
                <button className="btn btn-ghost" onClick={() => setShowInput(false)}>
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
