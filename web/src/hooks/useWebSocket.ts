import { useState, useEffect, useRef, useCallback } from 'react'
import { eventStats, eventTimeline, listEvents, listRelations } from '@/api'
import type { EventStats, EventItem, RelationItem, TimelinePoint } from '@/api'
import type { PipelineMessage } from '@/types'

const RECONNECT_BASE_DELAY_MS = 5000
const RECONNECT_MAX_DELAY_MS = 30000

export function useWebSocket() {
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
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/events`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      // 成功连接后重置重连退避间隔
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
      // 主动关闭或已被新连接替换的过期连接：短路，不再排定重连
      if (manualCloseRef.current || wsRef.current !== ws) return
      setConnected(false)
      const delay = reconnectDelayRef.current
      addLog(`[WebSocket 已断开，${Math.round(delay / 1000)}秒后重连...]`)
      reconnectTimerRef.current = setTimeout(() => {
        // 指数退避：每次翻倍，上限 30 秒
        reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_DELAY_MS)
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

export function useEventData() {
  const [stats, setStats] = useState<EventStats | null>(null)
  const [timeline, setTimeline] = useState<TimelinePoint[]>([])
  const [events, setEvents] = useState<EventItem[]>([])
  const [relations, setRelations] = useState<RelationItem[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [statsRes, timelineRes, eventsRes, relationsRes] = await Promise.all([
        eventStats(),
        eventTimeline({ query: { days: 30 } }),
        listEvents({ query: { limit: 100 } }),
        listRelations({ query: { limit: 50 } }),
      ])
      if (statsRes.data) setStats(statsRes.data)
      if (timelineRes.data?.timeline) setTimeline(timelineRes.data.timeline)
      if (eventsRes.data?.events) setEvents(eventsRes.data.events)
      if (relationsRes.data?.relations) setRelations(relationsRes.data.relations)
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return { stats, timeline, events, relations, loading, reload: load }
}