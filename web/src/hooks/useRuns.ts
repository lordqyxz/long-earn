import { useState, useEffect, useCallback } from 'react'
import { listRuns, runDashboard, symbolChart, symbolNames } from '@/api'
import type { RunInfo, DashboardData, SymbolChartData } from '@/api'

function isAbortError(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'AbortError'
}

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
      .then(({ data }) => {
        if (!controller.signal.aborted) setNames(data?.names ?? {})
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted && !isAbortError(e)) setNames({})
      })
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

    const controller = new AbortController()
    setLoading(true)
    setError(null)
    setData(null)

    runDashboard({ path: { run_id: runId }, signal: controller.signal })
      .then(({ data, error }) => {
        if (controller.signal.aborted) return
        if (error) throw new Error('加载回测详情失败')
        setData(data ?? null)
      })
      .catch((e: unknown) => {
        if (controller.signal.aborted || isAbortError(e)) return
        setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
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

    const controller = new AbortController()
    setLoading(true)

    symbolChart({
      path: { run_id: runId, symbol },
      signal: controller.signal,
    })
      .then(({ data }) => {
        if (!controller.signal.aborted) setData(data ?? null)
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted && !isAbortError(e)) setData(null)
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [runId, symbol])

  return { data, loading }
}
