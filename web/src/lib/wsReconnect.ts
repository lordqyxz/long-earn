/** WebSocket 重连退避与关闭守卫（事件管线 / 策略研发共用）。 */

export const WS_RECONNECT_MAX_DELAY_MS = 30_000

export function buildWsUrl(path: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}${path}`
}

export function nextReconnectDelay(
  currentMs: number,
  maxMs = WS_RECONNECT_MAX_DELAY_MS,
): number {
  return Math.min(currentMs * 2, maxMs)
}

/** 主动关闭或已被新连接替换的过期套接字：不应再排定重连。 */
export function shouldSkipReconnect(opts: {
  manualClose: boolean
  current: WebSocket | null
  closed: WebSocket
}): boolean {
  return opts.manualClose || opts.current !== opts.closed
}
