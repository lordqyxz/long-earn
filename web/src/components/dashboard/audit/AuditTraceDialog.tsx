import { useState, useEffect } from 'react'
import { Loader2, ScrollText } from 'lucide-react'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { runAuditEvent } from '@/api'
import type { AuditEventItem } from '@/api'
import { statusCls } from '@/components/dashboard/audit/format'

/**
 * 审计事件下钻弹窗：点击审计链节点后按需懒加载
 * GET /api/runs/{run_id}/audit/{trace_id} 的全部原始事件记录。
 */
export function AuditTraceDialog({
  runId,
  traceId,
  onClose,
}: {
  runId: string
  traceId: string | null
  onClose: () => void
}) {
  const [events, setEvents] = useState<AuditEventItem[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!traceId) return
    let stale = false
    setLoading(true)
    setError(null)
    setEvents(null)
    runAuditEvent({ path: { run_id: runId, trace_id: traceId } })
      .then(({ data, error: apiError }) => {
        if (stale) return
        if (apiError) throw new Error('加载审计事件失败')
        setEvents(data?.events ?? [])
      })
      .catch((e: unknown) => {
        if (!stale) setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!stale) setLoading(false)
      })
    return () => {
      stale = true
    }
  }, [runId, traceId])

  return (
    <Dialog
      open={Boolean(traceId)}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent className="flex max-h-[85vh] w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="shrink-0 border-b border-border px-5 py-4 pr-12">
          <DialogTitle className="flex items-center gap-2 text-sm font-semibold">
            <ScrollText className="h-4 w-4 shrink-0 text-primary" />
            审计事件详情
          </DialogTitle>
          <DialogDescription className="break-all font-mono text-[11px] leading-relaxed">
            {traceId ?? ''}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
              <span className="text-xs">正在拉取审计事件…</span>
            </div>
          ) : error ? (
            <div className="space-y-1.5 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-xs">
              <div className="font-medium text-destructive">加载失败：{error}</div>
              <div className="text-muted-foreground">请确认后端服务可用后重试；可关闭弹窗继续浏览。</div>
            </div>
          ) : !events || events.length === 0 ? (
            <div className="py-12 text-center text-xs text-muted-foreground">该 trace 无事件记录</div>
          ) : (
            <div className="space-y-3">
              <div className="text-[11px] text-muted-foreground">共 {events.length} 条事件记录</div>
              {events.map((ev, i) => (
                <div key={i} className="overflow-hidden rounded-lg border border-border bg-muted/20">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border/60 bg-muted/40 px-3 py-2">
                    <span className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] font-medium text-primary">
                      {ev.event_type || 'UNKNOWN'}
                    </span>
                    {ev.component && <span className="text-[11px] text-muted-foreground">{ev.component}</span>}
                    {ev.status && (
                      <span className={`font-mono text-[10px] font-semibold ${statusCls(ev.status)}`}>
                        {ev.status}
                      </span>
                    )}
                    <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground/70">
                      {ev.timestamp || '无时间戳'}
                    </span>
                  </div>
                  <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-all px-3 py-2.5 font-mono text-[11px] leading-relaxed text-foreground/90">
                    {JSON.stringify(ev.payload ?? {}, null, 2)}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
