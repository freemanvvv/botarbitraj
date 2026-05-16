import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import { Toaster } from 'react-hot-toast'
import App from './App'
import './styles.css'

ReactDOM.createRoot(document.getElementById('root')).render(
  <HashRouter>
    <Toaster position="top-center" toastOptions={{
      style: { background: '#1a1a3e', color: '#e0e0e0', border: '1px solid #333' }
    }} />
    <App />
  </HashRouter>
)
