import { useState, useEffect, useCallback } from 'react'
import { eventStats, listEvents } from '@/api'
import type { EventStats, EventItem } from '@/api'

export function useEventData() {
  const [stats, setStats] = useState<EventStats | null>(null)
  const [events, setEvents] = useState<EventItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [statsRes, eventsRes] = await Promise.all([
        eventStats(),
        listEvents({ query: { limit: 100 } }),
      ])

      const failures: string[] = []
      if (statsRes.error) failures.push('统计数据')
      if (eventsRes.error) failures.push('事件列表')
      if (failures.length > 0) {
        throw new Error(`${failures.join('、')}加载失败`)
      }

      setStats(statsRes.data ?? null)
      setEvents(eventsRes.data?.events ?? [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  return { stats, events, loading, error, reload: load }
}
