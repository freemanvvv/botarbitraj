import { useEffect } from 'react'
import { useBot, useWallet } from '../hooks/useApi'

export default function BotControl() {
  const { wallet, refresh: refreshWallet } = useWallet()
  const { status, loading, start, stop, refresh } = useBot()

  const isRunning = status.running

  const handleToggle = () => {
    if (isRunning) {
      stop()
    } else {
      start()
    }
  }

  return (
    <div className="page">
      <h2>🤖 Bot Control</h2>

      <div className="bot-card">
        <div className="bot-status-row">
          <span className={`bot-indicator ${isRunning ? 'running' : 'idle'}`} />
          <span className="bot-status-text">
            {isRunning ? '🟢 Running' : '⚪ Idle'}
          </span>
        </div>

        {isRunning && (
          <div className="bot-stats">
            <div className="stat">
              <span className="stat-value">3s</span>
              <span className="stat-label">Scan interval</span>
            </div>
            <div className="stat">
              <span className="stat-value">50 bps</span>
              <span className="stat-label">Slippage</span>
            </div>
            <div className="stat">
              <span className="stat-value">30 bps</span>
              <span className="stat-label">Min profit</span>
            </div>
          </div>
        )}

        <div className="bot-wallet-status">
          {wallet?.connected ? (
            <span className="wallet-ok">✅ Wallet: {wallet.address?.slice(0, 6)}...</span>
          ) : (
            <span className="wallet-missing">❌ Wallet not connected</span>
          )}
        </div>

        <button
          className={`btn btn-large ${isRunning ? 'btn-danger' : 'btn-primary'}`}
          onClick={handleToggle}
          disabled={loading || !wallet?.connected}
        >
          {loading ? '...' : isRunning ? '⏹ Stop Bot' : '▶ Start Bot'}
        </button>
      </div>
    </div>
  )
}
