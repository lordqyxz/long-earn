import { useMemo } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts'
import { CollapsibleSection } from '@/components/dashboard/CollapsibleSection'
import { TrendingUp } from 'lucide-react'
import { CHART_COLORS } from '@/lib/chart-colors'
import type { EquityPoint } from '@/api'

interface Props {
  equityCurve: EquityPoint[]
}

export function EquityChart({ equityCurve }: Props) {
  const chartData = useMemo(() => {
    if (equityCurve.length < 2) return []
    return equityCurve.map((p, i) => ({
      time: p.time?.slice(0, 10) || '',
      value: p.value,
      return: i === 0 ? 0 : ((p.value - equityCurve[i - 1].value) / equityCurve[i - 1].value) * 100,
    }))
  }, [equityCurve])

  // UI 显示统一保留两位小数
  const formatEquity = (v: number) =>
    `¥${v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
  const formatReturn = (v: number) => `${v.toFixed(2)}%`

  if (equityCurve.length < 2) {
    return (
      <CollapsibleSection title={<span className="flex items-center gap-2"><TrendingUp className="h-4 w-4" />权益曲线</span>}>
        <div className="text-center text-muted-foreground py-8">暂无数据</div>
      </CollapsibleSection>
    )
  }

  return (
    <CollapsibleSection title={<span className="flex items-center gap-2"><TrendingUp className="h-4 w-4" />权益曲线 & 日收益率</span>}>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 5, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} />
          <XAxis dataKey="time" tick={{ fontSize: 11, fill: CHART_COLORS.textMuted }} tickFormatter={(v) => v?.slice(0, 10) || ''} />
          <YAxis yAxisId="equity" orientation="left" tick={{ fontSize: 11, fill: CHART_COLORS.textMuted }} tickFormatter={(v) => `¥${((v ?? 0) / 10000).toFixed(2)}万`} />
          <YAxis yAxisId="return" orientation="right" tick={{ fontSize: 11, fill: CHART_COLORS.textMuted }} tickFormatter={(v) => `${(v ?? 0).toFixed(2)}%`} />
          <Tooltip
            contentStyle={{ background: CHART_COLORS.tooltipBg, border: `1px solid ${CHART_COLORS.tooltipBorder}`, borderRadius: '8px', fontSize: '12px', color: CHART_COLORS.textBright }}
            labelStyle={{ color: CHART_COLORS.textMuted }}
            formatter={(value, name) => {
              if (name === '权益') return [formatEquity(Number(value)), name]
              if (name === '日收益%') return [formatReturn(Number(value)), name]
              return [String(value), name]
            }}
          />
          <Legend />
          <Line yAxisId="equity" type="monotone" dataKey="value" name="权益" stroke="#2563eb" strokeWidth={2} dot={false} />
          <Bar yAxisId="return" dataKey="return" name="日收益%" fill="#86efac" opacity={0.6} />
        </ComposedChart>
      </ResponsiveContainer>
    </CollapsibleSection>
  )
}