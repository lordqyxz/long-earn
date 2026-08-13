import { Shield, AlertTriangle, ArrowDown, Calendar, TrendingUp, Activity } from 'lucide-react'
import { CollapsibleSection } from '@/components/dashboard/CollapsibleSection'
import { formatPercent } from '@/lib/utils'
import type { RiskMetrics, Benchmark } from '@/api'

interface Props {
  risk: RiskMetrics | null
  benchmark: Benchmark | null
}

export function RiskMetricsPanel({ risk, benchmark }: Props) {
  if (!risk) return null

  const hasBenchmark = benchmark != null
    && (benchmark.alpha != null || benchmark.beta != null
        || benchmark.information_ratio != null || benchmark.tracking_error != null)

  const riskItems = [
    { label: '年化收益率', value: formatPercent(risk.annual_return ?? 0), icon: <TrendingUp className="h-3.5 w-3.5" /> },
    { label: '年化波动率', value: formatPercent(risk.annual_volatility ?? 0), icon: <Activity className="h-3.5 w-3.5" /> },
    { label: '夏普比率', value: (risk.sharpe_ratio ?? 0).toFixed(2), icon: <Shield className="h-3.5 w-3.5" /> },
    { label: '最大回撤', value: formatPercent(risk.max_drawdown ?? 0), icon: <ArrowDown className="h-3.5 w-3.5" /> },
    { label: '回撤持续', value: `${risk.max_drawdown_duration_days ?? 0} 天`, icon: <Calendar className="h-3.5 w-3.5" /> },
    { label: 'VaR 95%', value: formatPercent(risk.var_95 ?? 0), icon: <AlertTriangle className="h-3.5 w-3.5" /> },
    { label: 'VaR 99%', value: formatPercent(risk.var_99 ?? 0), icon: <AlertTriangle className="h-3.5 w-3.5" /> },
    { label: 'CVaR 95%', value: formatPercent(risk.cvar_95 ?? 0), icon: <AlertTriangle className="h-3.5 w-3.5" /> },
  ]

  return (
    <CollapsibleSection
      title={<span className="flex items-center gap-2"><Shield className="h-4 w-4" />风险指标</span>}
      contentClassName="p-0"
    >
      <div className="grid grid-cols-4 gap-px bg-border">
        {riskItems.map((item) => (
          <div key={item.label} className="bg-card p-3">
            <div className="flex items-center gap-1.5 text-muted-foreground mb-1">
              {item.icon}
              <span className="text-xs">{item.label}</span>
            </div>
            <div className="text-sm font-mono font-semibold">{item.value}</div>
          </div>
        ))}
      </div>
      {hasBenchmark && benchmark && (
        <div className="grid grid-cols-4 gap-px bg-border border-t border-border">
          <div className="bg-card p-3">
            <div className="text-xs text-muted-foreground mb-1">Alpha</div>
            <div className={`text-sm font-mono font-semibold ${(benchmark.alpha ?? 0) >= 0 ? 'text-success' : 'text-destructive'}`}>
              {formatPercent(benchmark.alpha ?? 0)}
            </div>
          </div>
          <div className="bg-card p-3">
            <div className="text-xs text-muted-foreground mb-1">Beta</div>
            <div className="text-sm font-mono font-semibold">{(benchmark.beta ?? 0).toFixed(2)}</div>
          </div>
          <div className="bg-card p-3">
            <div className="text-xs text-muted-foreground mb-1">信息比率</div>
            <div className="text-sm font-mono font-semibold">{(benchmark.information_ratio ?? 0).toFixed(2)}</div>
          </div>
          <div className="bg-card p-3">
            <div className="text-xs text-muted-foreground mb-1">跟踪误差</div>
            <div className="text-sm font-mono font-semibold">{formatPercent(benchmark.tracking_error ?? 0)}</div>
          </div>
        </div>
      )}
    </CollapsibleSection>
  )
}