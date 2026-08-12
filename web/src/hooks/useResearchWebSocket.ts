import { useState, useRef, useCallback, useEffect } from 'react'
import type { ResearchState, ResearchEvent, RoundMetrics } from '@/types/research'

const WS_URL = `ws://${window.location.host}/ws/research`

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

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setState((s) => ({ ...s, connected: true, error: null }))
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
      setState((s) => ({ ...s, connected: false }))
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  const disconnect = useCallback(() => {
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current)
    }
    wsRef.current?.close()
    wsRef.current = null
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