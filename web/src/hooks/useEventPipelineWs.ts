import { useState, useEffect, useRef, useCallback } from 'react'
import type { PipelineMessage } from '@/types'
import {
  buildWsUrl,
  nextReconnectDelay,
  shouldSkipReconnect,
} from '@/lib/wsReconnect'

const RECONNECT_BASE_DELAY_MS = 5000

export function useEventPipelineWs() {
  const [connected, setConnected] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const [pipelineStage, setPipelineStage] = useState<string>('idle')
  const [pipelineProgress, setPipelineProgress] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)
  const manualCloseRef = useRef(false)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const reconnectDelayRef = useRef(RECONNECT_BASE_DELAY_MS)

  const addLog = useCallback((msg: string) => {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    setLog((prev) => [...prev.slice(-99), `[${time}] ${msg}`])
  }, [])

  const connect = useCallback(() => {
    manualCloseRef.current = false
    const ws = new WebSocket(buildWsUrl('/ws/events'))
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      reconnectDelayRef.current = RECONNECT_BASE_DELAY_MS
      addLog('[WebSocket 已连接]')
      ws.send(JSON.stringify({ action: 'subscribe' }))
    }

    ws.onmessage = (event) => {
      let msg: PipelineMessage
      try {
        msg = JSON.parse(event.data)
      } catch {
        addLog('[收到无法解析的消息，已忽略]')
        return
      }
      switch (msg.type) {
        case 'subscribed':
          addLog('[已订阅事件流]')
          break
        case 'pipeline_start':
          addLog(`[管线启动: ${msg.query}]`)
          setPipelineStage('collect')
          setPipelineProgress(0)
          break
        case 'pipeline_progress':
          addLog(`[${msg.stage}] ${msg.detail}`)
          setPipelineStage(msg.stage || '')
          setPipelineProgress(msg.progress || 0)
          break
        case 'pipeline_complete':
          addLog(`[完成] ${msg.message || '管线执行完成'}`)
          setPipelineStage('done')
          setPipelineProgress(100)
          break
        case 'pipeline_error':
          addLog(`[error] ${msg.detail}`)
          setPipelineStage('error')
          break
        case 'pong':
          break
      }
    }

    ws.onclose = () => {
      if (shouldSkipReconnect({
        manualClose: manualCloseRef.current,
        current: wsRef.current,
        closed: ws,
      })) return
      setConnected(false)
      const delay = reconnectDelayRef.current
      addLog(`[WebSocket 已断开，${Math.round(delay / 1000)}秒后重连...]`)
      reconnectTimerRef.current = setTimeout(() => {
        reconnectDelayRef.current = nextReconnectDelay(delay)
        connect()
      }, delay)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [addLog])

  useEffect(() => {
    connect()
    return () => {
      manualCloseRef.current = true
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = undefined
      }
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [connect])

  const triggerPipeline = useCallback((query: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ action: 'trigger', query }))
  }, [])

  const reloadData = useCallback(() => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({ action: 'reload' }))
  }, [])

  return { connected, log, pipelineStage, pipelineProgress, triggerPipeline, reloadData }
}
