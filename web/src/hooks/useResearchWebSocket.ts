import { useState, useRef, useCallback, useEffect } from 'react'
import type { ResearchState, ResearchEvent, RoundMetrics } from '@/types/research'

const WS_PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
const WS_URL = `${WS_PROTOCOL}//${window.location.host}/ws/research`

const RECONNECT_BASE_DELAY_MS = 3000
const RECONNECT_MAX_DELAY_MS = 30000

const INITIAL_STATE: ResearchState = {
  connected: false,
  running: false,
  idea: '',
  maxRounds: 3,
  currentRound: 0,
  events: [],
  rounds: [],
  bestRecentReturn: -999,
  stagnationCount: 0,
  familyIdx: 0,
  completed: false,
  error: null,
}

export function useResearchWebSocket() {
  const [state, setState] = useState<ResearchState>(INITIAL_STATE)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()
  const manualCloseRef = useRef(false)
  const reconnectDelayRef = useRef(RECONNECT_BASE_DELAY_MS)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    manualCloseRef.current = false

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setState((s) => ({ ...s, connected: true, error: null }))
      // 成功连接后重置重连退避间隔
      reconnectDelayRef.current = RECONNECT_BASE_DELAY_MS
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = undefined
      }
    }

    ws.onmessage = (event) => {
      try {
        const data: ResearchEvent = JSON.parse(event.data)
        setState((prev) => applyEvent(prev, data))
      } catch {
        // ignore parse errors
      }
    }

    ws.onclose = () => {
      // 主动关闭或已被新连接替换的过期连接：短路，不再排定重连
      if (manualCloseRef.current || wsRef.current !== ws) return
      setState((s) => ({ ...s, connected: false }))
      const delay = reconnectDelayRef.current
      reconnectTimer.current = setTimeout(() => {
        // 指数退避：每次翻倍，上限 30 秒
        reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_DELAY_MS)
        connect()
      }, delay)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  const disconnect = useCallback(() => {
    manualCloseRef.current = true
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current)
      reconnectTimer.current = undefined
    }
    const ws = wsRef.current
    wsRef.current = null
    ws?.close()
  }, [])

  const startResearch = useCallback((idea: string, maxRounds = 3, maxIterations = 2, minImprovement = 0.005) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return
    wsRef.current.send(JSON.stringify({
      action: 'start',
      idea,
      max_rounds: maxRounds,
      max_iterations: maxIterations,
      min_improvement: minImprovement,
    }))
  }, [])

  const reset = useCallback(() => {
    setState(INITIAL_STATE)
  }, [])

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  return { state, startResearch, reset }
}

function applyEvent(prev: ResearchState, event: ResearchEvent): ResearchState {
  switch (event.type) {
    case 'research_started':
      return {
        ...prev,
        running: true,
        idea: event.idea ?? prev.idea,
        maxRounds: event.max_rounds ?? prev.maxRounds,
        currentRound: 0,
        rounds: [],
        events: [],
        completed: false,
        error: null,
      }

    case 'round_start':
      return {
        ...prev,
        currentRound: event.round ?? prev.currentRound,
        familyIdx: event.family_idx ?? prev.familyIdx,
        events: [...prev.events, event],
      }

    case 'round_complete': {
      const metrics = event.metrics as RoundMetrics | undefined
      const newRounds = metrics
        ? [...prev.rounds.filter((r) => r.round !== metrics.round), metrics].sort((a, b) => a.round - b.round)
        : prev.rounds
      return {
        ...prev,
        rounds: newRounds,
        bestRecentReturn: (event.best_recent_return ?? prev.bestRecentReturn),
        stagnationCount: event.stagnation_count ?? prev.stagnationCount,
        events: [...prev.events, event],
      }
    }

    case 'family_switch':
      return {
        ...prev,
        familyIdx: event.family_idx ?? prev.familyIdx,
        events: [...prev.events, event],
      }

    case 'research_complete':
      return {
        ...prev,
        running: false,
        completed: true,
        events: [...prev.events, event],
      }

    case 'research_error':
      return {
        ...prev,
        running: false,
        error: event.detail ?? '未知错误',
        events: [...prev.events, event],
      }

    default:
      return prev
  }
}