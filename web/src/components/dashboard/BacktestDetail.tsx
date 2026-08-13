import { useState, useMemo, useEffect } from 'react'
import { Loader2, ArrowUp, ArrowDown, BarChart3, Info } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { MetricCards } from '@/components/dashboard/MetricCards'
import { RiskMetricsPanel } from '@/components/dashboard/RiskMetrics'
import { EquityChart } from '@/components/dashboard/EquityChart'
import { SymbolChart } from '@/components/dashboard/SymbolChart'
import { CollapsibleSection } from '@/components/dashboard/CollapsibleSection'
import { SymbolDetailDialog } from '@/components/dashboard/SymbolDetailDialog'
import { useDashboard, useSymbolChart, useSymbolNames } from '@/hooks/useRuns'
import { formatDate, formatNumber } from '@/lib/utils'
import type { TradeRecord } from '@/api'

interface Props {
  runId: string
}

export function BacktestDetail({ runId }: Props) {
  const { data, loading } = useDashboard(runId)

  // 交易标的选择
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  // 公司信息弹窗
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null)
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
              <TableHead className="text-right">价格</TableHead>
              <TableHead className="text-right">数量</TableHead>
              <TableHead className="text-right">金额</TableHead>
              <TableHead>状态</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(data.trade_journal ?? []).length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="text-center text-muted-foreground py-8">暂无交易记录</TableCell>
              </TableRow>
            ) : (
              (data.trade_journal ?? []).slice(-50).reverse().map((t: TradeRecord, i: number) => {
                const name = symbolNames[t.symbol]
                const isBuy = t.type === 'BUY'
                return (
                  <TableRow key={t.trace_id || i}>
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
                    <TableCell className="text-right font-mono text-xs">¥{formatNumber(t.price ?? 0)}</TableCell>
                    <TableCell className="text-right text-xs">{t.quantity ?? 0}</TableCell>
                    <TableCell className="text-right font-mono text-xs">¥{formatNumber((t.price ?? 0) * (t.quantity ?? 0), 0)}</TableCell>
                    <TableCell>
                      <span className="inline-flex items-center rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground">
                        成交
                      </span>
                    </TableCell>
                  </TableRow>
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
    </div>
  )
}
