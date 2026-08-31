import { useState, useEffect, useCallback } from 'react'
import { listRuns } from '@/api'
import type { RunInfo } from '@/api'

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
