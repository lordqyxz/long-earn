import { useState, useMemo, useEffect, Fragment } from 'react'
import type { ReactNode } from 'react'
import {
  Loader2, ArrowUp, ArrowDown, BarChart3, Info, ChevronDown, ChevronRight, ArrowRight,
  Zap, ShieldAlert, CheckCircle2, ClipboardList, GitBranch, Filter, ListOrdered, Scale,
  Sigma, Database, Clock, ScrollText,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { MetricCards } from '@/components/dashboard/MetricCards'
import { RiskMetricsPanel } from '@/components/dashboard/RiskMetrics'
import { EquityChart } from '@/components/dashboard/EquityChart'
import { SymbolChart } from '@/components/dashboard/SymbolChart'
import { CollapsibleSection } from '@/components/dashboard/CollapsibleSection'
import { SymbolDetailDialog } from '@/components/dashboard/SymbolDetailDialog'
import { useDashboard, useSymbolChart, useSymbolNames } from '@/hooks/useRuns'
import { formatDate, formatNumber } from '@/lib/utils'
import { runAuditEvent } from '@/api'
import type { AuditChainEvent, AuditEventItem, TradeAttribution, TradeRecord } from '@/api'

interface Props {
  runId: string
}

const ATTR_KIND_LABEL: Record<string, string> = {
  signal: '信号驱动',
  risk: '风控触发',
  direct: '高级订单',
  pending: '待成交订单',
  unknown: '未知',
}

const RISK_TYPE_LABEL: Record<string, string> = {
  stop_loss: '止损触发',
  take_profit: '止盈触发',
  max_drawdown: '最大回撤清仓',
}

/** kind -> 徽章视觉（图标 + 语义配色，与设计系统 token 一致） */
const KIND_BADGE: Record<string, { icon: LucideIcon; cls: string }> = {
  signal: { icon: Zap, cls: 'border-primary/25 bg-primary/10 text-primary' },
  risk: { icon: ShieldAlert, cls: 'border-warning/40 bg-warning/10 text-warning' },
  direct: { icon: ClipboardList, cls: 'border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-300' },
  pending: { icon: Clock, cls: 'border-border bg-muted text-muted-foreground' },
}

/** 因子彩色标签色板（按注册顺序轮转；/10 底 + /30 边框，亮暗主题均可读） */
const FACTOR_PALETTE = [
  'border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-300',
  'border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-300',
  'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-300',
  'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300',
  'border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-300',
  'border-cyan-500/30 bg-cyan-500/10 text-cyan-600 dark:text-cyan-300',
]

/** 未在色板注册的因子列（如多列算子输出）使用的中性标签样式 */
const NEUTRAL_TAG_CLS = 'border-border bg-muted text-muted-foreground'

function fmtNum(v: unknown, digits = 2): string {
  if (typeof v !== 'number') return v != null ? String(v) : '-'
  return v.toFixed(digits)
}

function fmtPct(v: unknown): string {
  if (typeof v !== 'number') return fmtNum(v)
  return `${(v * 100).toFixed(1)}%`
}

/** 百分比数值着色：正绿负红零中性 */
function pctCls(v: unknown): string {
  if (typeof v !== 'number') return 'text-foreground'
  if (v > 0) return 'text-success'
  if (v < 0) return 'text-destructive'
  return 'text-foreground'
}

// ============ 审计归因面板：辅助小组件 ============

/** 归因 kind 徽章：图标 + 语义色，未知 kind 回退中性灰 */
function KindBadge({ kind }: { kind?: string }) {
  const k = kind ?? ''
  const meta = KIND_BADGE[k]
  const label = ATTR_KIND_LABEL[k] || k || '未知'
  if (!meta) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] text-muted-foreground">
        {label}
      </span>
    )
  }
  const Icon = meta.icon
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${meta.cls}`}>
      <Icon className="h-3 w-3" />
      {label}
    </span>
  )
}

/** 因子来源彩色小标签（同一别名全面板同色：流水线 / 过滤 / 排名 / 选股表联动） */
function FactorTag({ alias, cls }: { alias: string; cls: string }) {
  return (
    <span className={`inline-flex items-center rounded border px-1 py-px font-mono text-[10px] leading-4 ${cls}`}>
      {alias}
    </span>
  )
}

/** criteria 渲染段（后端下发的结构化渲染数据）。
 * type 保持宽松（string | undefined）：后端未来新增段类型时前端应优雅降级
 * 为 text 渲染，而不是因类型不匹配而崩溃。
 */
interface RenderSegment {
  type?: string
  value?: string | number | boolean
  unit?: string
}

/** criteria 单步的结构（types.gen 中为内联结构，此处显式声明供小组件 props 使用） */
interface Criterion {
  step?: string
  op?: string
  alias?: string
  params?: Record<string, unknown>
  desc?: string
  format?: string
  /** 粗粒度步骤类型 → 图标；未知回退 factor 样式 */
  kind?: string
  /** 有序渲染段：新数据走数据驱动渲染，旧数据（无此字段）回退 desc 文本 */
  segments?: RenderSegment[]
}

/** kind -> 胶囊图标：factor/filter/rank 为后端契约的小型分类，未知回退 Sigma */
const KIND_ICON: Record<string, LucideIcon> = {
  factor: Sigma,
  filter: Filter,
  rank: ListOrdered,
}

function kindIcon(kind?: string): LucideIcon {
  return KIND_ICON[kind ?? ''] ?? Sigma
}

/**
 * 单个算子步骤胶囊（决策流水线的一环）——**数据驱动渲染**：
 * 后端下发 ``kind``（选图标）+ ``segments``（有序渲染段），前端零算子知识：
 * - ``field`` 段 → 因子彩色标签（字段名变更无需改前端）
 * - ``value`` / ``symbol`` 段 → 等宽文本
 * - ``text`` 段 → 普通文本
 * 新增算子只需后端下发模板，前端无需同步分支。
 * 旧数据（无 segments）回退：factor 步骤用 alias + desc，其余显示 desc 文本。
 * hover title 展示原始 op + params，供审计核验。
 */
function CriterionChip({ c, factorCls }: { c: Criterion; factorCls: Map<string, string> }) {
  const op = c.op ?? ''
  const params = c.params ?? {}
  const title = `op: ${op}(${JSON.stringify(params)})${c.desc ? `\n${c.desc}` : ''}`
  const base =
    'inline-flex items-center gap-1.5 rounded-md border border-border bg-muted/40 px-2 py-1 text-[11px] text-foreground/90 transition-colors hover:border-foreground/25 hover:bg-muted'
  const fieldTag = (field: string): ReactNode =>
    field && factorCls.has(field) ? (
      <FactorTag alias={field} cls={factorCls.get(field) ?? NEUTRAL_TAG_CLS} />
    ) : (
      <span className="font-mono text-[11px]">{field}</span>
    )
  const renderSegment = (seg: RenderSegment, i: number): ReactNode => {
    switch (seg.type ?? 'text') {
      case 'field':
        return <Fragment key={i}>{fieldTag(String(seg.value ?? ''))}</Fragment>
      case 'value':
        return (
          <span key={i} className="font-mono text-[11px]">
            {String(seg.value ?? '')}
            {seg.unit ?? ''}
          </span>
        )
      case 'symbol':
        return (
          <span key={i} className="font-mono text-[11px]">
            {String(seg.value ?? '')}
          </span>
        )
      default:
        return <span key={i}>{String(seg.value ?? '')}</span>
    }
  }
  let content: ReactNode
  if (c.segments && c.segments.length > 0) {
    // 新数据：后端下发的结构化段，动态渲染（零算子知识）
    const Icon = kindIcon(c.kind)
    content = (
      <>
        <Icon className="h-3 w-3 shrink-0 text-muted-foreground/50" />
        {c.step === 'factor' && c.alias && (
          <FactorTag alias={c.alias} cls={factorCls.get(c.alias) ?? NEUTRAL_TAG_CLS} />
        )}
        {c.segments.map(renderSegment)}
      </>
    )
  } else if (c.step === 'factor') {
    // 旧数据回退：factor 步骤保留 alias 标签 + desc（去掉重复的 "alias = " 前缀）
    const alias = c.alias ?? ''
    let body = c.desc ?? op
    if (alias && body.startsWith(`${alias} = `)) body = body.slice(alias.length + 3)
    content = (
      <>
        <Sigma className="h-3 w-3 shrink-0 text-muted-foreground/50" />
        {alias && <FactorTag alias={alias} cls={factorCls.get(alias) ?? NEUTRAL_TAG_CLS} />}
        <span>{body}</span>
      </>
    )
  } else {
    // 旧数据回退：信号步骤显示 desc 文本（不按 op 名解读 params）
    content = <span>{c.desc || `${op}(…)`}</span>
  }
  return (
    <span className={base} title={title}>
      {content}
    </span>
  )
}

/** 风控详情数值项：label + 等宽值（可着色） */
function RiskStat({ label, value, valueCls }: { label: string; value: string; valueCls?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`font-mono text-xs ${valueCls ?? 'text-foreground'}`}>{value}</div>
    </div>
  )
}

/** 审计事件 status -> 语义色：SUCCESS 绿 / WARNING 琥珀 / ERROR·FAIL 红 / 其余中性 */
function statusCls(status: string): string {
  const s = status.toUpperCase()
  if (s.includes('SUCCESS') || s === 'OK') return 'text-success'
  if (s.includes('WARN')) return 'text-warning'
  if (s.includes('ERROR') || s.includes('FAIL')) return 'text-destructive'
  return 'text-muted-foreground'
}

/**
 * 审计链节点胶囊：图标 + 环节名 + trace id。
 * - hover：Tooltip 显示后端预计算的紧凑摘要（第一行醒目等宽）+ 事件元信息 + 时间戳；无摘要时退化为 trace id
 * - 点击：onOpenTrace(traceId) 触发下钻弹窗查看该 trace 的全部原始审计事件
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
        {/* 第一行：摘要醒目等宽；无摘要退化为 trace id */}
        <div className="font-mono text-xs font-semibold text-foreground">{summary || traceId || '-'}</div>
        {/* 第二行：事件元信息（status 语义着色） */}
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
        {/* 第三行：时间戳 */}
        {event?.timestamp && (
          <div className="mt-0.5 font-mono text-[10px] text-muted-foreground/70">{event.timestamp}</div>
        )}
        {/* 有摘要时补完整 trace id（保留原 title 悬浮核验能力，可选中复制） */}
        {summary && traceId && (
          <div className="mt-1 break-all border-t border-border pt-1 font-mono text-[10px] text-muted-foreground/60">
            {traceId}
          </div>
        )}
      </TooltipContent>
    </Tooltip>
  )
}

/** 审计链：水平节点路径图（信号/风控触发 -> 订单 -> 成交），<details> 可折叠；节点 hover 摘要 / 点击下钻 */
function AuditChain({ att, onOpenTrace }: { att: TradeAttribution; onOpenTrace?: (traceId: string) => void }) {
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

// ============ 审计事件下钻弹窗 ============

/**
 * 审计事件下钻弹窗：点击审计链节点后按需懒加载
 * GET /api/runs/{run_id}/audit/{trace_id} 的全部原始事件记录（含完整 payload）。
 * - loading 态：旋转指示
 * - 失败态：错误信息 + 可关闭
 * - 成功态：逐条事件元信息（event_type / component / status / timestamp）+ <pre> 等宽 payload
 *   内容区域 max-h + overflow-auto（payload 可能很大，单条内部再独立滚动）
 */
function AuditTraceDialog({ runId, traceId, onClose }: { runId: string; traceId: string | null; onClose: () => void }) {
  const [events, setEvents] = useState<AuditEventItem[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // traceId 变化时懒加载该 trace 的全部审计事件（stale 标记防止竞态写入旧结果）
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

// ============ 审计归因面板主体 ============

/** 单笔交易的审计归因明细（SIGNAL->ORDER->FILL / RISK_TRIGGER->ORDER->FILL） */
function TradeAttributionDetail({ att, onOpenTrace }: { att?: TradeAttribution | null; onOpenTrace?: (traceId: string) => void }) {
  if (!att) return null
  const risk = att.risk_trigger
  const signal = att.signal
  const rationale = signal?.rationale
  const criteria = rationale?.criteria ?? []
  const selection = rationale && Array.isArray(rationale.selection) ? rationale.selection : []
  // 因子别名 -> 数值格式（pct=百分比，其余按原值），供选股依据列渲染
  const fmtMap: Record<string, string> = {}
  for (const c of criteria) {
    if (c.alias && c.format) fmtMap[c.alias] = c.format
  }
  // 因子别名 -> 彩色标签样式：criteria 的 factor 步骤先注册，selection 首次出现的列按序补齐
  const factorCls = new Map<string, string>()
  const registerFactor = (alias: string) => {
    if (alias && !factorCls.has(alias)) {
      factorCls.set(alias, FACTOR_PALETTE[factorCls.size % FACTOR_PALETTE.length])
    }
  }
  for (const c of criteria) {
    if (c.step === 'factor' && c.alias) registerFactor(c.alias)
  }
  for (const s of selection) {
    for (const k of Object.keys(s)) {
      if (k !== 'symbol' && k !== 'rank') registerFactor(k)
    }
  }
  const fmtVal = (v: unknown, alias: string) => {
    if (typeof v !== 'number') return String(v ?? '-')
    if (fmtMap[alias] === 'pct') return `${(v * 100).toFixed(1)}%`
    return v.toFixed(2)
  }
  // pct 类因子值正绿负红，其余按前景色
  const valCls = (v: unknown, alias: string) => (fmtMap[alias] === 'pct' ? pctCls(v) : 'text-foreground')
  const riskType = risk ? String(risk.risk_type ?? '') : ''
  const orderType = att.order?.type
  return (
    <div className="space-y-2 py-1 text-xs">
      {/* 头部：kind 徽章 + 策略 / 风控介入 / 订单摘要 */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <KindBadge kind={att.kind} />
        {signal?.strategy_id && (
          <span className="text-[11px] text-muted-foreground">
            策略 <span className="font-mono text-foreground">{signal.strategy_id}</span>
          </span>
        )}
        {signal?.risk_triggered === true && (
          <span className="inline-flex items-center gap-1 rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-[10px] text-warning">
            <ShieldAlert className="h-3 w-3" />
            当日风控介入
          </span>
        )}
        {att.order && (
          <span className="text-[11px] text-muted-foreground">
            订单{' '}
            <span
              className={`font-mono ${
                orderType === 'BUY' ? 'text-success' : orderType === 'SELL' ? 'text-destructive' : 'text-foreground'
              }`}
            >
              {orderType} {att.order.symbol} ×{att.order.quantity}
            </span>
          </span>
        )}
      </div>

      {/* signal 类：决策流水线 + 选股依据（主面板） */}
      {rationale && (
        <div className="space-y-2.5 rounded-lg border border-border bg-muted/20 p-2.5">
          {/* 选股漏斗：候选 -> 选中 */}
          {(rationale.universe_size != null || rationale.selected_count != null) && (
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted-foreground">
              <Database className="h-3 w-3 text-muted-foreground/50" />
              <span>
                候选 <span className="font-mono text-foreground">{rationale.universe_size ?? '-'}</span>
              </span>
              <ArrowRight className="h-3 w-3 text-muted-foreground/40" />
              <span>
                选中 <span className="font-mono text-foreground">{rationale.selected_count ?? '-'}</span>
              </span>
            </div>
          )}
          {/* 决策流水线：因子 -> 过滤 -> 排名 -> ... -> 权重方法 */}
          {(criteria.length > 0 || rationale.weights?.method) && (
            <div className="flex flex-wrap items-center gap-x-1 gap-y-1.5">
              {criteria.map((c, i) => (
                <Fragment key={i}>
                  {i > 0 && <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />}
                  <CriterionChip c={c} factorCls={factorCls} />
                </Fragment>
              ))}
              {criteria.length > 0 && <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground/40" />}
              {rationale.weights?.method && (
                <span
                  className="inline-flex items-center gap-1 rounded-full border border-primary/25 bg-primary/5 px-2 py-1 text-[11px] font-medium text-primary"
                  title={`权重方法: ${rationale.weights.method}`}
                >
                  <Scale className="h-3 w-3" />
                  {rationale.weights.method === 'equal' ? '等权' : rationale.weights.method}
                </span>
              )}
            </div>
          )}
          {/* 选股依据：每行排名 + 标的 + 彩色因子标签值 */}
          {selection.length > 0 && (
            <div className="space-y-1">
              <div className="text-[11px] text-muted-foreground">选股依据（按排名）</div>
              <div className="space-y-0.5">
                {selection.map((s, i) => {
                  const sym = String(s.symbol ?? '')
                  const rank = typeof s.rank === 'number' ? s.rank : null
                  const factors = Object.entries(s).filter(([k]) => k !== 'symbol' && k !== 'rank')
                  return (
                    <div
                      key={sym || i}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded px-1 py-0.5 transition-colors hover:bg-muted/60"
                    >
                      {rank != null && (
                        <span className="inline-flex h-4 w-5 shrink-0 items-center justify-center rounded-sm bg-primary/10 font-mono text-[10px] text-primary">
                          #{rank}
                        </span>
                      )}
                      <span className="min-w-[88px] shrink-0 font-mono text-xs text-foreground">{sym}</span>
                      {factors.map(([k, v]) => (
                        <span key={k} className="inline-flex items-center gap-1" title={`${k} = ${fmtVal(v, k)}`}>
                          <FactorTag alias={k} cls={factorCls.get(k) ?? NEUTRAL_TAG_CLS} />
                          <span className={`font-mono text-[11px] ${valCls(v, k)}`}>{fmtVal(v, k)}</span>
                        </span>
                      ))}
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          {/* 公式原文（弱化展示，保底可审计） */}
          {rationale.formula && (
            <div className="border-t border-border/60 pt-1.5 text-[10px] leading-relaxed text-muted-foreground/60">
              公式原文：{rationale.formula}
            </div>
          )}
        </div>
      )}

      {/* signal 类无 rationale：当日信号选股权重 */}
      {!rationale && signal?.signals && Object.keys(signal.signals).length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">当日信号选股：</span>
          {Object.entries(signal.signals).map(([s, w]) => (
            <span
              key={s}
              className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/40 px-2 py-0.5"
            >
              <span className="font-mono text-[11px] text-foreground">{s}</span>
              <span className="font-mono text-[10px] text-muted-foreground">{fmtPct(w)}</span>
            </span>
          ))}
        </div>
      )}

      {/* risk 类：风控触发详情（数值网格面板） */}
      {risk && riskType in RISK_TYPE_LABEL && (
        <div className="space-y-2 rounded-lg border border-warning/30 bg-warning/5 p-2.5">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-warning" />
            <span className="text-xs font-medium text-foreground">{RISK_TYPE_LABEL[riskType]}</span>
            <span className="font-mono text-[10px] text-muted-foreground/70">{riskType}</span>
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
            {riskType === 'stop_loss' && (
              <>
                <RiskStat label="盈亏幅度" value={fmtPct(risk.pnl_pct)} valueCls={pctCls(risk.pnl_pct)} />
                <RiskStat label="成本价" value={fmtNum(risk.avg_cost)} />
                <RiskStat label="触发价" value={fmtNum(risk.check_price)} />
                <RiskStat label="成交参考价" value={fmtNum(risk.ref_price)} />
                <RiskStat label="止损线" value={`-${fmtNum(risk.stop_loss_threshold, 0)}%`} />
                <RiskStat label="委托数量" value={fmtNum(risk.quantity, 0)} />
              </>
            )}
            {riskType === 'take_profit' && (
              <>
                <RiskStat label="盈亏幅度" value={fmtPct(risk.pnl_pct)} valueCls={pctCls(risk.pnl_pct)} />
                <RiskStat label="成本价" value={fmtNum(risk.avg_cost)} />
                <RiskStat label="触发价" value={fmtNum(risk.check_price)} />
                <RiskStat label="止盈线" value={`+${fmtNum(risk.take_profit_threshold, 0)}%`} />
                <RiskStat label="委托数量" value={fmtNum(risk.quantity, 0)} />
              </>
            )}
            {riskType === 'max_drawdown' && (
              <>
                <RiskStat label="组合回撤" value={fmtPct(risk.drawdown)} valueCls={pctCls(risk.drawdown)} />
                <RiskStat label="组合峰值" value={`¥${fmtNum(risk.peak_value, 0)}`} />
                <RiskStat label="当前净值" value={`¥${fmtNum(risk.total_value, 0)}`} />
                <RiskStat label="回撤限制" value={`-${fmtNum(risk.max_drawdown_limit, 0)}%`} />
              </>
            )}
          </div>
        </div>
      )}

      {/* 审计链：trace id 路径图（节点可 hover / 点击下钻） */}
      <AuditChain att={att} onOpenTrace={onOpenTrace} />
    </div>
  )
}

export function BacktestDetail({ runId }: Props) {
  const { data, loading } = useDashboard(runId)

  // 交易标的选择
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  // 公司信息弹窗
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null)
  // 交易明细中展开审计归因的行（trace_id）
  const [expandedTrace, setExpandedTrace] = useState<string | null>(null)
  // 审计链节点下钻：当前打开的 trace id（弹窗内懒加载该 trace 的全部原始事件）
  const [auditTrace, setAuditTrace] = useState<string | null>(null)
  const tradedSymbols = useMemo(() => data?.traded_symbols || [], [data])
  const { data: symbolData, loading: symbolLoading } = useSymbolChart(runId, selectedSymbol)

  // 批量获取标的中文名
  const symbolNames = useSymbolNames(tradedSymbols)

  // 自动选中第一个标的
  useEffect(() => {
    if (tradedSymbols.length > 0 && !selectedSymbol) {
      setSelectedSymbol(tradedSymbols[0])
    }
  }, [tradedSymbols, selectedSymbol])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    )
  }

  if (!data) {
    return (
      <Card>
        <CardContent className="text-center text-muted-foreground py-12">
          加载失败或运行数据为空
        </CardContent>
      </Card>
    )
  }

  return (
    <TooltipProvider delayDuration={150}>
      <div className="p-4 space-y-4 overflow-auto">
      {/* 指标卡片 */}
      <CollapsibleSection title="指标概览" defaultOpen={true}>
        <MetricCards data={data} />
      </CollapsibleSection>

      {/* 风险指标 */}
      <RiskMetricsPanel risk={data.risk_metrics ?? null} benchmark={data.benchmark ?? null} />

      {/* 权益曲线 */}
      <EquityChart equityCurve={data.equity_curve ?? []} />

      {/* 交易标的 + 个股图表 */}
      <CollapsibleSection title={<span className="flex items-center gap-2"><BarChart3 className="h-4 w-4" />交易标的</span>}>
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          {/* 标的列表 */}
          <div className="lg:col-span-1 max-h-[320px] overflow-auto">
            {tradedSymbols.length === 0 ? (
              <div className="text-center text-muted-foreground py-4 text-sm">暂无交易标的</div>
            ) : (
              <div className="space-y-0.5">
                {tradedSymbols.map((sym) => {
                  const name = symbolNames[sym]
                  const isSelected = selectedSymbol === sym
                  return (
                    <div
                      key={sym}
                      className={`group flex items-center gap-1 px-2 py-2 rounded-md transition-colors ${
                        isSelected
                          ? 'bg-primary text-primary-foreground font-medium'
                          : 'hover:bg-muted text-muted-foreground'
                      }`}
                    >
                      <button
                        onClick={() => setSelectedSymbol(sym)}
                        className="flex-1 text-left"
                      >
                        <div className="text-sm truncate">{name || sym}</div>
                        {name && (
                          <div className={`font-mono text-xs truncate ${isSelected ? 'text-primary-foreground/70' : 'text-muted-foreground/70'}`}>
                            {sym}
                          </div>
                        )}
                      </button>
                      <button
                        onClick={() => setDetailSymbol(sym)}
                        className={`shrink-0 p-1 rounded transition-opacity ${
                          isSelected
                            ? 'text-primary-foreground/60 hover:text-primary-foreground'
                            : 'text-muted-foreground/40 hover:text-muted-foreground'
                        }`}
                        title="查看公司信息"
                      >
                        <Info className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* 个股图表 */}
          <div className="lg:col-span-3">
            <SymbolChart
              data={symbolData}
              loading={symbolLoading}
              symbolName={selectedSymbol ? symbolNames[selectedSymbol] : undefined}
            />
          </div>
        </div>
      </CollapsibleSection>

      {/* 交易明细 */}
      <CollapsibleSection title="交易明细" defaultOpen={true} contentClassName="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>交易日</TableHead>
              <TableHead>标的</TableHead>
              <TableHead>方向</TableHead>
              <TableHead>原因</TableHead>
              <TableHead className="text-right">价格</TableHead>
              <TableHead className="text-right">数量</TableHead>
              <TableHead className="text-right">金额</TableHead>
              <TableHead>状态</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data.trade_journal ?? []).length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground py-8">暂无交易记录</TableCell>
              </TableRow>
            ) : (
              (data.trade_journal ?? []).slice(-50).reverse().map((t: TradeRecord, i: number) => {
                const name = symbolNames[t.symbol]
                const isBuy = t.type === 'BUY'
                const expanded = expandedTrace === t.trace_id
                return (
                  <>
                    <TableRow key={t.trace_id || i} className={expanded ? 'bg-muted/30' : undefined}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">{formatDate(t.time)}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <div className="flex flex-col">
                          <span className="text-xs">{name || t.symbol}</span>
                          {name && <span className="font-mono text-[11px] text-muted-foreground">{t.symbol}</span>}
                        </div>
                        <button
                          onClick={() => setDetailSymbol(t.symbol)}
                          className="text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                          title="查看公司信息"
                        >
                          <Info className="h-3 w-3" />
                        </button>
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
                        isBuy
                          ? 'bg-success/15 text-success'
                          : 'bg-destructive/15 text-destructive'
                      }`}>
                        {isBuy ? <ArrowUp className="h-3 w-3" /> : <ArrowDown className="h-3 w-3" />}
                        {isBuy ? '买入' : '卖出'}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs max-w-[220px]">
                      {t.reason ? (
                        <button
                          onClick={() => setExpandedTrace(expanded ? null : (t.trace_id || null))}
                          disabled={!t.attribution}
                          className={`inline-flex max-w-full items-center gap-1 text-left line-clamp-1 transition-colors ${
                            t.attribution
                              ? 'cursor-pointer text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground'
                              : 'text-muted-foreground'
                          }`}
                          title={t.attribution ? '点击查看审计归因' : t.reason}
                        >
                          <span className="truncate">{t.reason}</span>
                          {t.attribution && (
                            <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
                          )}
                        </button>
                      ) : (
                        <span className="text-muted-foreground/50">-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs">¥{formatNumber(t.price ?? 0)}</TableCell>
                    <TableCell className="text-right text-xs">{t.quantity ?? 0}</TableCell>
                    <TableCell className="text-right font-mono text-xs">¥{formatNumber((t.price ?? 0) * (t.quantity ?? 0), 0)}</TableCell>
                    <TableCell>
                      <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
                        成交
                      </span>
                    </TableCell>
                  </TableRow>
                  {expanded && (
                    <TableRow key={`${t.trace_id}-attr`}>
                      <TableCell colSpan={8} className="px-4 py-2">
                        <TradeAttributionDetail att={t.attribution} onOpenTrace={setAuditTrace} />
                      </TableCell>
                    </TableRow>
                  )}
                  </>
                )
              })
            )}
          </TableBody>
        </Table>
      </CollapsibleSection>

      {/* 公司信息弹窗 */}
      <SymbolDetailDialog
        symbol={detailSymbol}
        onClose={() => setDetailSymbol(null)}
      />

      {/* 审计链节点下钻弹窗（懒加载原始审计事件） */}
      <AuditTraceDialog
        runId={runId}
        traceId={auditTrace}
        onClose={() => setAuditTrace(null)}
      />
      </div>
    </TooltipProvider>
  )
}
