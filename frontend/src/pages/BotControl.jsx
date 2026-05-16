import { useBot, useWallet } from '../hooks/useApi'

export default function BotControl() {
  const { wallet } = useWallet()
  const { status, loading, start, stop } = useBot()

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
      <div className="info-block">
        <p>🤖 <strong>Управление ботом.</strong> Запускает автоматическое сканирование и исполнение сделок.</p>
        <p>Параметры: сканирование каждые 3с, мин. профит 30 bps (0.3%), проскальзывание 50 bps (0.5%).</p>
      </div>

      <div className="bot-card">
        <div className="bot-status-row">
          <span className={`bot-indicator ${isRunning ? 'running' : 'idle'}`} />
          <span className="bot-status-text">
            {isRunning ? '🟢 Запущен' : '⏸ Остановлен'}
          </span>
        </div>

        {isRunning && (
          <div className="bot-stats">
            <div className="stat">
              <span className="stat-value">3с</span>
              <span className="stat-label">Интервал</span>
            </div>
            <div className="stat">
              <span className="stat-value">50</span>
              <span className="stat-label">Slippage</span>
            </div>
            <div className="stat">
              <span className="stat-value">30</span>
              <span className="stat-label">Мин. профит</span>
            </div>
          </div>
        )}

        <div className="bot-wallet-status">
          {wallet?.connected ? (
            <span className="wallet-ok" style={{ color: '#00ff88' }}>
              ✅ Кошелёк: {wallet.address?.slice(0, 6)}...
            </span>
          ) : (
            <span className="wallet-missing" style={{ color: '#ff6644' }}>
              ❌ Кошелёк не подключён (нужен для сделок)
            </span>
          )}
        </div>

        <button
          className={`btn btn-large ${isRunning ? 'btn-danger' : 'btn-primary'}`}
          onClick={handleToggle}
          disabled={loading || !wallet?.connected}
        >
          {loading ? '⏳' : isRunning ? '⏹ Остановить' : '▶ Запустить бота'}
        </button>
      </div>
    </div>
  )
}
