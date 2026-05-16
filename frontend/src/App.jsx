import { Routes, Route, Link, useLocation } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Scanner from './pages/Scanner'
import Wallet from './pages/Wallet'
import Trades from './pages/Trades'
import BotControl from './pages/BotControl'

export default function App() {
  const location = useLocation()

  const navItems = [
    { path: '/', label: '📊', title: 'Dashboard' },
    { path: '/scanner', label: '🔍', title: 'Scanner' },
    { path: '/wallet', label: '💳', title: 'Wallet' },
    { path: '/bot', label: '🤖', title: 'Bot' },
    { path: '/trades', label: '📜', title: 'Trades' },
  ]

  return (
    <div className="app">
      <header className="header">
        <h1 className="logo">SolArb</h1>
        <span className="subtitle">Solana Arbitrage Bot</span>
      </header>

      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/scanner" element={<Scanner />} />
          <Route path="/wallet" element={<Wallet />} />
          <Route path="/bot" element={<BotControl />} />
          <Route path="/trades" element={<Trades />} />
        </Routes>
      </main>

      <nav className="bottom-nav">
        {navItems.map(item => (
          <Link
            key={item.path}
            to={item.path}
            className={`nav-item ${location.pathname === item.path ? 'active' : ''}`}
            title={item.title}
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </div>
  )
}
