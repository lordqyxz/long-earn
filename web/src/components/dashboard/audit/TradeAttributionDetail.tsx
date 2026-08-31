import { Fragment } from 'react'
import type { ReactNode } from 'react'
import {
  ArrowRight, ChevronRight, ClipboardList, Clock, Database, Filter, ListOrdered,
  Scale, ShieldAlert, Sigma, Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { TradeAttribution } from '@/api'
import { AuditChain } from '@/components/dashboard/audit/AuditChain'
import { fmtNum, fmtPct, pctCls } from '@/components/dashboard/audit/format'

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

/** 因子彩色标签色板（按注册顺序轮转） */
const FACTOR_PALETTE = [
  'border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-300',
  'border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-300',
  'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-300',
  'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300',
  'border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-300',
  'border-cyan-500/30 bg-cyan-500/10 text-cyan-600 dark:text-cyan-300',
]

const NEUTRAL_TAG_CLS = 'border-border bg-muted text-muted-foreground'

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

function FactorTag({ alias, cls }: { alias: string; cls: string }) {
  return (
    <span className={`inline-flex items-center rounded border px-1 py-px font-mono text-[10px] leading-4 ${cls}`}>
      {alias}
    </span>
  )
}

interface RenderSegment {
  type?: string
  value?: string | number | boolean
  unit?: string
}

interface Criterion {
  step?: string
  op?: string
  alias?: string
  params?: Record<string, unknown>
  desc?: string
  format?: string
  kind?: string
  segments?: RenderSegment[]
}

const KIND_ICON: Record<string, LucideIcon> = {
  factor: Sigma,
  filter: Filter,
  rank: ListOrdered,
}

function kindIcon(kind?: string): LucideIcon {
  return KIND_ICON[kind ?? ''] ?? Sigma
}

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
    content = <span>{c.desc || `${op}(…)`}</span>
  }
  return (
    <span className={base} title={title}>
      {content}
    </span>
  )
}

function RiskStat({ label, value, valueCls }: { label: string; value: string; valueCls?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] text-muted-foreground">{label}</div>
      <div className={`font-mono text-xs ${valueCls ?? 'text-foreground'}`}>{value}</div>
    </div>
  )
}

/** 单笔交易的审计归因明细 */
export function TradeAttributionDetail({
  att,
  onOpenTrace,
}: {
  att?: TradeAttribution | null
  onOpenTrace?: (traceId: string) => void
}) {
  if (!att) return null
  const risk = att.risk_trigger
  const signal = att.signal
  const rationale = signal?.rationale
  const criteria = rationale?.criteria ?? []
  const selection = rationale && Array.isArray(rationale.selection) ? rationale.selection : []
  const fmtMap: Record<string, string> = {}
  for (const c of criteria) {
    if (c.alias && c.format) fmtMap[c.alias] = c.format
  }
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
  const valCls = (v: unknown, alias: string) => (fmtMap[alias] === 'pct' ? pctCls(v) : 'text-foreground')
  const riskType = risk ? String(risk.risk_type ?? '') : ''
  const orderType = att.order?.type
  return (
    <div className="space-y-2 py-1 text-xs">
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

      {rationale && (
        <div className="space-y-2.5 rounded-lg border border-border bg-muted/20 p-2.5">
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
          {rationale.formula && (
            <div className="border-t border-border/60 pt-1.5 text-[10px] leading-relaxed text-muted-foreground/60">
              公式原文：{rationale.formula}
            </div>
          )}
        </div>
      )}

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

      <AuditChain att={att} onOpenTrace={onOpenTrace} />
    </div>
  )
}
