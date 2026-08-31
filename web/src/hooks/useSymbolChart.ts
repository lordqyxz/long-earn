import { useState, useEffect } from 'react'
import { symbolChart } from '@/api'
import type { SymbolChartData } from '@/api'

function isAbortError(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'AbortError'
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
