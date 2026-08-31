export function fmtNum(v: unknown, digits = 2): string {
  if (typeof v !== 'number') return v != null ? String(v) : '-'
  return v.toFixed(digits)
}

export function fmtPct(v: unknown): string {
  if (typeof v !== 'number') return fmtNum(v)
  return `${(v * 100).toFixed(1)}%`
}

/** 百分比数值着色：正绿负红零中性 */
export function pctCls(v: unknown): string {
  if (typeof v !== 'number') return 'text-foreground'
  if (v > 0) return 'text-success'
  if (v < 0) return 'text-destructive'
  return 'text-foreground'
}

/** 审计事件 status -> 语义色：SUCCESS 绿 / WARNING 琥珀 / ERROR·FAIL 红 / 其余中性 */
export function statusCls(status: string): string {
  const s = status.toUpperCase()
  if (s.includes('SUCCESS') || s === 'OK') return 'text-success'
  if (s.includes('WARN')) return 'text-warning'
  if (s.includes('ERROR') || s.includes('FAIL')) return 'text-destructive'
  return 'text-muted-foreground'
}
