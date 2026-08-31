import { Fragment } from 'react'
import { ArrowUp, ArrowDown, ChevronDown, Info } from 'lucide-react'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { TradeAttributionDetail } from '@/components/dashboard/audit/TradeAttributionDetail'
import { formatDate, formatNumber } from '@/lib/utils'
import type { TradeRecord } from '@/api'

interface Props {
  trades: TradeRecord[]
  symbolNames: Record<string, string>
  expandedTrace: string | null
  onToggleTrace: (traceId: string | null) => void
  onOpenDetail: (symbol: string) => void
  onOpenTrace: (traceId: string) => void
}

export function TradeJournal({
  trades,
  symbolNames,
  expandedTrace,
  onToggleTrace,
  onOpenDetail,
  onOpenTrace,
}: Props) {
  return (
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
        {trades.length === 0 ? (
          <TableRow>
            <TableCell colSpan={8} className="text-center text-muted-foreground py-8">暂无交易记录</TableCell>
          </TableRow>
        ) : (
          trades.slice(-50).reverse().map((t: TradeRecord, i: number) => {
            const name = symbolNames[t.symbol]
            const isBuy = t.type === 'BUY'
            const expanded = expandedTrace === t.trace_id
            return (
              <Fragment key={t.trace_id || i}>
                <TableRow className={expanded ? 'bg-muted/30' : undefined}>
                  <TableCell className="text-xs text-muted-foreground whitespace-nowrap">{formatDate(t.time)}</TableCell>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <div className="flex flex-col">
                        <span className="text-xs">{name || t.symbol}</span>
                        {name && <span className="font-mono text-[11px] text-muted-foreground">{t.symbol}</span>}
                      </div>
                      <button
                        onClick={() => onOpenDetail(t.symbol)}
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
                        onClick={() => onToggleTrace(expanded ? null : (t.trace_id || null))}
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
                  <TableRow>
                    <TableCell colSpan={8} className="px-4 py-2">
                      <TradeAttributionDetail att={t.attribution} onOpenTrace={onOpenTrace} />
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            )
          })
        )}
      </TableBody>
    </Table>
  )
}
