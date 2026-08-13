import { useState, useEffect, useCallback } from 'react'
import { listRuns, runDashboard, symbolChart, symbolNames } from '@/api'
import type { RunInfo, DashboardData, SymbolChartData } from '@/api'

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
    symbolNames({ query: { symbols: key }, signal: controller.signal })
      .then(({ data }) => setNames(data?.names ?? {}))
      .catch(() => setNames({}))
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      const { data, error } = await listRuns()
      if (error) throw new Error('获取回测列表失败')
      setRuns(data?.runs ?? [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
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
    runDashboard({ path: { run_id: runId } })
      .then(({ data, error }) => {
        if (error) throw new Error('加载回测详情失败')
        setData(data ?? null)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
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
    symbolChart({ path: { run_id: runId, symbol } })
      .then(({ data }) => setData(data ?? null))
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [runId, symbol])

  return { data, loading }
}
