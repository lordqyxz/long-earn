import { useState, useEffect } from 'react'
import { symbolNames } from '@/api'

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
