import { useState, useMemo, useEffect } from 'react'
import { Loader2, BarChart3 } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { TooltipProvider } from '@/components/ui/tooltip'
import { MetricCards } from '@/components/dashboard/MetricCards'
import { RiskMetricsPanel } from '@/components/dashboard/RiskMetrics'
import { EquityChart } from '@/components/dashboard/EquityChart'
import { CollapsibleSection } from '@/components/ui/collapsible-section'
import { SymbolDetailDialog } from '@/components/dashboard/SymbolDetailDialog'
import { TradedSymbolsPanel } from '@/components/dashboard/trades/TradedSymbolsPanel'
import { TradeJournal } from '@/components/dashboard/trades/TradeJournal'
import { AuditTraceDialog } from '@/components/dashboard/audit/AuditTraceDialog'
import { useDashboard } from '@/hooks/useDashboard'
import { useSymbolChart } from '@/hooks/useSymbolChart'
import { useSymbolNames } from '@/hooks/useSymbolNames'

interface Props {
  runId: string
}

export function BacktestDetail({ runId }: Props) {
  const { data, loading } = useDashboard(runId)

  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [detailSymbol, setDetailSymbol] = useState<string | null>(null)
  const [expandedTrace, setExpandedTrace] = useState<string | null>(null)
  const [auditTrace, setAuditTrace] = useState<string | null>(null)
  const tradedSymbols = useMemo(() => data?.traded_symbols || [], [data])
  const { data: symbolData, loading: symbolLoading } = useSymbolChart(runId, selectedSymbol)
  const symbolNames = useSymbolNames(tradedSymbols)

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
        <CollapsibleSection title="指标概览" defaultOpen={true}>
          <MetricCards data={data} />
        </CollapsibleSection>

        <RiskMetricsPanel risk={data.risk_metrics ?? null} benchmark={data.benchmark ?? null} />

        <EquityChart equityCurve={data.equity_curve ?? []} />

        <CollapsibleSection title={<span className="flex items-center gap-2"><BarChart3 className="h-4 w-4" />交易标的</span>}>
          <TradedSymbolsPanel
            tradedSymbols={tradedSymbols}
            selectedSymbol={selectedSymbol}
            symbolNames={symbolNames}
            symbolData={symbolData}
            symbolLoading={symbolLoading}
            onSelect={setSelectedSymbol}
            onOpenDetail={setDetailSymbol}
          />
        </CollapsibleSection>

        <CollapsibleSection title="交易明细" defaultOpen={true} contentClassName="p-0">
          <TradeJournal
            trades={data.trade_journal ?? []}
            symbolNames={symbolNames}
            expandedTrace={expandedTrace}
            onToggleTrace={setExpandedTrace}
            onOpenDetail={setDetailSymbol}
            onOpenTrace={setAuditTrace}
          />
        </CollapsibleSection>

        <SymbolDetailDialog
          symbol={detailSymbol}
          onClose={() => setDetailSymbol(null)}
        />

        <AuditTraceDialog
          runId={runId}
          traceId={auditTrace}
          onClose={() => setAuditTrace(null)}
        />
      </div>
    </TooltipProvider>
  )
}
