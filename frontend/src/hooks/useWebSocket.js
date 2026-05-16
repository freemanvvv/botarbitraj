import { useState, useEffect, useCallback } from 'react'

// Vite proxy handles /ws in dev. In production, use VITE_WS_URL.
function getDefaultWsUrl() {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL
  return `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/api/ws`
}

export function useWebSocket(url = getDefaultWsUrl()) {
  const [connected, setConnected] = useState(false)
  const [lastMessage, setLastMessage] = useState(null)

  useEffect(() => {
    let ws
    let reconnectTimer

    function connect() {
      ws = new WebSocket(url)
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        reconnectTimer = setTimeout(connect, 3000)
      }
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          setLastMessage(data)
        } catch { /* ignore */ }
      }
      ws.onerror = () => { /* handled by onclose */ }
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [url])

  return { connected, lastMessage }
}
