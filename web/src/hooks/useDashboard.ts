import { useState, useEffect } from 'react'
import { runDashboard } from '@/api'
import type { DashboardData } from '@/api'

function isAbortError(e: unknown): boolean {
  return e instanceof DOMException && e.name === 'AbortError'
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
