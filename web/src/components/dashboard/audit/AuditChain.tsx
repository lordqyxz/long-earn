import { ArrowRight, CheckCircle2, ChevronRight, ClipboardList, GitBranch, ShieldAlert, Zap } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import type { AuditChainEvent, TradeAttribution } from '@/api'
import { statusCls } from '@/components/dashboard/audit/format'

/**
 * 审计链节点胶囊：图标 + 环节名 + trace id。
 * - hover：Tooltip 显示后端预计算的紧凑摘要 + 事件元信息 + 时间戳
 * - 点击：onOpenTrace(traceId) 触发下钻弹窗
 */
function TraceNode({
  icon: Icon,
  label,
  traceId,
  cls,
  event,
  onOpenTrace,
}: {
  icon: LucideIcon
  label: string
  traceId?: string
  cls: string
  event?: AuditChainEvent | null
  onOpenTrace?: (traceId: string) => void
}) {
  const clickable = Boolean(traceId && onOpenTrace)
  const summary = event?.summary?.trim()
  const status = event?.status
  const hasMeta = Boolean(event?.event_type || event?.component || status)
  const inner = (
    <>
      <Icon className="h-3 w-3 shrink-0" />
      <span className="shrink-0 text-[10px] font-medium">{label}</span>
      <span className="min-w-0 truncate font-mono text-[9px] opacity-70">{traceId || '-'}</span>
    </>
  )
  const baseCls = `inline-flex max-w-[240px] items-center gap-1.5 rounded-md border px-2 py-1 ${cls}`
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {clickable ? (
          <button
            type="button"
            onClick={() => {
              if (traceId && onOpenTrace) onOpenTrace(traceId)
            }}
            className={`${baseCls} cursor-pointer transition-[filter,box-shadow] hover:brightness-110 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50`}
          >
            {inner}
          </button>
        ) : (
          <span className={baseCls}>{inner}</span>
        )}
      </TooltipTrigger>
      <TooltipContent side="top">
        <div className="font-mono text-xs font-semibold text-foreground">{summary || traceId || '-'}</div>
        {hasMeta && (
          <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
            {event?.event_type && <span className="font-mono">{event.event_type}</span>}
            {event?.component && (
              <>
                {event?.event_type && <span>·</span>}
                <span>{event.component}</span>
              </>
            )}
            {status && (
              <>
                {hasMeta && (event?.event_type || event?.component) && <span>·</span>}
                <span className={`font-medium ${statusCls(status)}`}>{status}</span>
              </>
            )}
          </div>
        )}
        {event?.timestamp && (
          <div className="mt-0.5 font-mono text-[10px] text-muted-foreground/70">{event.timestamp}</div>
        )}
        {summary && traceId && (
          <div className="mt-1 break-all border-t border-border pt-1 font-mono text-[10px] text-muted-foreground/60">
            {traceId}
          </div>
        )}
      </TooltipContent>
    </Tooltip>
  )
}

/** 审计链：水平节点路径图（信号/风控触发 -> 订单 -> 成交） */
export function AuditChain({ att, onOpenTrace }: { att: TradeAttribution; onOpenTrace?: (traceId: string) => void }) {
  const chain = att.chain
  if (!chain) return null
  const events = chain.events
  const isRisk = att.kind === 'risk'
  return (
    <details className="group mt-0.5">
      <summary className="inline-flex cursor-pointer select-none items-center gap-1 text-[10px] text-muted-foreground/60 transition-colors hover:text-muted-foreground [&::-webkit-details-marker]:hidden">
        <GitBranch className="h-3 w-3" />
        审计链（悬浮看摘要 · 点击节点查原始事件）
        <ChevronRight className="h-3 w-3 transition-transform group-open:rotate-90" />
      </summary>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <TraceNode
          icon={isRisk ? ShieldAlert : Zap}
          label={isRisk ? '风控触发' : '信号'}
          traceId={chain.upstream}
          cls={
            isRisk
              ? 'border-warning/40 bg-warning/10 text-warning'
              : 'border-primary/25 bg-primary/10 text-primary'
          }
          event={events?.upstream}
          onOpenTrace={onOpenTrace}
        />
        <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />
        <TraceNode
          icon={ClipboardList}
          label="订单"
          traceId={chain.order}
          cls="border-border bg-muted/40 text-foreground"
          event={events?.order}
          onOpenTrace={onOpenTrace}
        />
        <ArrowRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />
        <TraceNode
          icon={CheckCircle2}
          label="成交"
          traceId={chain.fill}
          cls="border-success/30 bg-success/10 text-success"
          event={events?.fill}
          onOpenTrace={onOpenTrace}
        />
      </div>
    </details>
  )
}
