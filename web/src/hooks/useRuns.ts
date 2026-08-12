import { useState, useEffect, useCallback } from 'react'
import type { RunInfo, DashboardData, SymbolChartData } from '@/types'

const API_BASE = '/api'

/** 批量获取标的中文名映射 */
export function useSymbolNames(symbols: string[]): Record<string, string> {
  const [names, setNames] = useState<Record<string, string>>({})

  useEffect(() => {
    if (symbols.length === 0) {
      setNames({})
      return
    }
    const controller = new AbortController()
    const key = symbols.join(',')
    fetch(`${API_BASE}/symbols/names?symbols=${encodeURIComponent(key)}`, {
      signal: controller.signal,
    })
      .then((res) => res.json())
      .then((data) => setNames(data.names || {}))
      .catch(() => setNames({}))
    return () => controller.abort()
  }, [symbols.join(',')])

  return names
}

export function useRuns() {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadRuns = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/runs`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setRuns(data.runs || [])
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadRuns() }, [loadRuns])

  return { runs, loading, error, reload: loadRuns }
}

export function useDashboard(runId: string | null) {
  const [data, setData] = useState<DashboardData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!runId) {
      setData(null)
      return
    }
    setLoading(true)
    setError(null)
    setData(null)  // 切换 run 时清空旧数据
    fetch(`${API_BASE}/runs/${runId}/dashboard`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [runId])

  return { data, loading, error }
}

export function useSymbolChart(runId: string | null, symbol: string | null) {
  const [data, setData] = useState<SymbolChartData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!runId || !symbol) {
      setData(null)
      return
    }
    setLoading(true)
    fetch(`${API_BASE}/runs/${runId}/symbol/${symbol}/chart`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [runId, symbol])

  return { data, loading }
}
