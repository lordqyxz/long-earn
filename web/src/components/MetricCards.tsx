import { TrendingUp, TrendingDown, Minus, DollarSign, BarChart3, Activity, Zap } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { DashboardData } from '@/types'

interface Props {
  data: DashboardData | null
}

const iconMap: Record<string, React.ReactNode> = {
  total_return: <TrendingUp className="h-4 w-4" />,
  final_equity: <DollarSign className="h-4 w-4" />,
  trade_count: <BarChart3 className="h-4 w-4" />,
  event_count: <Activity className="h-4 w-4" />,
  sharpe_ratio: <Zap className="h-4 w-4" />,
  max_drawdown: <TrendingDown className="h-4 w-4" />,
}

export function MetricCards({ data }: Props) {
  if (!data) return null

  const equity = data.equity_curve
  const finalEquity = equity.length > 0 ? equity[equity.length - 1].value : 0
  const initialEquity = equity.length > 0 ? equity[0].value : 0
  const totalReturn = data.risk_metrics?.total_return ?? 0

  const metrics = [
    {
      label: '总收益率',
      value: `${(totalReturn * 100).toFixed(2)}%`,
      trend: totalReturn >= 0 ? ('up' as const) : ('down' as const),
      icon: 'total_return',
    },
    {
      label: '最终权益',
      value: `¥${finalEquity.toLocaleString('zh-CN', { maximumFractionDigits: 0 })}`,
      trend: finalEquity >= initialEquity ? ('up' as const) : ('down' as const),
      icon: 'final_equity',
    },
    {
      label: '交易次数',
      value: `${data.trade_journal?.length ?? 0}`,
      trend: 'neutral' as const,
      icon: 'trade_count',
    },
    {
      label: '事件总数',
      value: `${data.total_events ?? 0}`,
      trend: 'neutral' as const,
      icon: 'event_count',
    },
    {
      label: '夏普比率',
      value: (data.risk_metrics?.sharpe_ratio ?? 0).toFixed(2),
      trend: (data.risk_metrics?.sharpe_ratio ?? 0) >= 1 ? ('up' as const) : ('neutral' as const),
      icon: 'sharpe_ratio',
    },
    {
      label: '最大回撤',
      value: `${((data.risk_metrics?.max_drawdown ?? 0) * 100).toFixed(2)}%`,
      trend: 'down' as const,
      icon: 'max_drawdown',
    },
  ]

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {metrics.map((m) => {
        const trendColor =
          m.trend === 'up' ? 'text-success' : m.trend === 'down' ? 'text-destructive' : 'text-muted-foreground'
        return (
          <Card key={m.label} className="hover:border-primary/30 transition-colors">
            <CardContent className="p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-muted-foreground">{m.label}</span>
                <span className={cn(trendColor)}>{iconMap[m.icon]}</span>
              </div>
              <div className={cn('text-lg font-bold', trendColor)}>
                {m.value}
              </div>
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}