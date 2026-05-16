import { useState, useEffect } from 'react'
import toast from 'react-hot-toast'

// Vite proxy handles /api in dev. In production, set VITE_API_URL.
const BASE = import.meta.env.VITE_API_URL || '/api'

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || 'Request failed')
  }
  return res.json()
}

export function useApi() {
  const [loading, setLoading] = useState(false)

  const get = async (path) => {
    setLoading(true)
    try {
      return await request(path)
    } finally {
      setLoading(false)
    }
  }

  const post = async (path, body) => {
    setLoading(true)
    try {
      return await request(path, {
        method: 'POST',
        body: JSON.stringify(body),
      })
    } finally {
      setLoading(false)
    }
  }

  return { get, post, loading }
}

export function usePrices() {
  const [prices, setPrices] = useState({})
  const [loading, setLoading] = useState(true)

  const fetch = async () => {
    try {
      const data = await request('/prices')
      setPrices(data)
    } catch { /* ignore */ }
    setLoading(false)
  }

  useEffect(() => { fetch() }, [])

  return { prices, loading, refresh: fetch }
}

export function useScan() {
  const [results, setResults] = useState({ all: [], profitable: [] })
  const [loading, setLoading] = useState(false)

  const scan = async () => {
    setLoading(true)
    try {
      const data = await request('/scan')
      setResults(data)
      if (data.profitable?.length > 0) {
        toast.success(`🔥 ${data.profitable.length} opportunity(s) found!`, { duration: 4000 })
      }
    } catch { /* ignore */ }
    setLoading(false)
  }

  return { results, loading, scan }
}

export function useWallet() {
  const [wallet, setWallet] = useState(null)
  const [loading, setLoading] = useState(false)

  const fetchStatus = async () => {
    try {
      const data = await request('/wallet')
      setWallet(data)
    } catch { /* ignore */ }
  }

  const connect = async (privateKey) => {
    setLoading(true)
    try {
      const data = await post('/wallet/connect', { privateKey })
      setWallet(data)
      toast.success('✅ Wallet connected!')
      return data
    } catch (err) {
      toast.error(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchStatus() }, [])

  return { wallet, loading, connect, refresh: fetchStatus }
}

export function useBot() {
  const [status, setStatus] = useState({ running: false, status: 'idle' })
  const [loading, setLoading] = useState(false)

  const fetchStatus = async () => {
    try {
      const data = await request('/bot/status')
      setStatus(data)
    } catch { /* ignore */ }
  }

  const start = async () => {
    setLoading(true)
    try {
      const data = await post('/bot/start')
      setStatus({ running: true, status: 'running' })
      toast.success('🤖 Bot started!')
      return data
    } catch (err) {
      toast.error(err.message)
    } finally {
      setLoading(false)
    }
  }

  const stop = async () => {
    setLoading(true)
    try {
      const data = await post('/bot/stop')
      setStatus({ running: false, status: 'idle' })
      toast('⏹ Bot stopped')
      return data
    } catch (err) {
      toast.error(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchStatus() }, [])

  return { status, loading, start, stop, refresh: fetchStatus }
}
