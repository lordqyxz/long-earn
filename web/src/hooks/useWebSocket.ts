import { useState, useEffect, useRef, useCallback } from 'react'
import type { PipelineMessage, EventStats, EventItem, RelationItem, TimelinePoint } from '@/types'

const API_BASE = '/api'

export function useWebSocket() {
  const [connected, setConnected] = useState(false)
  const [log, setLog] = useState<string[]>([])
  const [pipelineStage, setPipelineStage] = useState<string>('idle')
  const [pipelineProgress, setPipelineProgress] = useState(0)
  const wsRef = useRef<WebSocket | null>(null)

  const addLog = useCallback((msg: string) => {
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    setLog((prev) => [...prev.slice(-99), `[${time}] ${msg}`])
  }, [])

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/ws/events`
    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      addLog('[WebSocket 已连接]')
      ws.send(JSON.stringify({ action: 'subscribe' }))
    }

    ws.onmessage = (event) => {
      const msg: PipelineMessage = JSON.parse(event.data)
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
      setConnected(false)
      addLog('[WebSocket 已断开，5秒后重连...]')
      setTimeout(connect, 5000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [addLog])

  useEffect(() => {
    connect()
    return () => wsRef.current?.close()
  }, [connect])

  const triggerPipeline = useCallback((query: string) => {
    wsRef.current?.send(JSON.stringify({ action: 'trigger', query }))
  }, [])

  const reloadData = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ action: 'reload' }))
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
        fetch(`${API_BASE}/events/stats`),
        fetch(`${API_BASE}/events/timeline?days=30`),
        fetch(`${API_BASE}/events?limit=100`),
        fetch(`${API_BASE}/events/relations?limit=50`),
      ])
      if (statsRes.ok) setStats(await statsRes.json())
      if (timelineRes.ok) {
        const t = await timelineRes.json()
        setTimeline(t.timeline || [])
      }
      if (eventsRes.ok) {
        const e = await eventsRes.json()
        setEvents(e.events || [])
      }
      if (relationsRes.ok) {
        const r = await relationsRes.json()
        setRelations(r.relations || [])
      }
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return { stats, timeline, events, relations, loading, reload: load }
}