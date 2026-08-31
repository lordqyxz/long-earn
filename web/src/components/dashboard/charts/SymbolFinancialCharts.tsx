import { useMemo } from 'react'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  Cell,
} from 'recharts'
import { CHART_COLORS } from '@/lib/chart-colors'
import type { FinancialRecord } from '@/hooks/useSymbolDetail'

interface Props {
  financials: FinancialRecord[]
}

// ── 配色 ──
const COLORS = {
  revenue: CHART_COLORS.up,
  netProfit: CHART_COLORS.up,
  netLoss: CHART_COLORS.down,
  roe: '#8b5cf6',
  margin: '#f59e0b',
  grid: CHART_COLORS.grid,
  textMuted: CHART_COLORS.textMuted,
  textBright: CHART_COLORS.textBright,
  // 现金流配色
  ocf: CHART_COLORS.up,
  investingCf: '#3b82f6',
  financingCf: '#f59e0b',
  capex: '#94a3b8',
  fcfPositive: CHART_COLORS.up,
  fcfNegative: CHART_COLORS.down,
}

export function SymbolFinancialCharts({ financials }: Props) {
  // 财务数据按时间升序排列（图表用）
  const sortedFinancials = useMemo(
    () => [...financials].sort((a, b) => a.report_date.localeCompare(b.report_date)),
    [financials],
  )

  // 格式化财报日期为短标签 + 计算自由现金流
  const chartData = useMemo(
    () =>
      sortedFinancials.map((f) => ({
        ...f,
        label: f.report_date?.slice(0, 7) || '',
        fcf: (f.ocf || 0) - (f.capex || 0),
      })),
    [sortedFinancials],
  )

  return (
    <div className="space-y-4">
      {/* 营业收入 */}
      <FinanceChartCard title="营业收入" unit="亿">
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${(v / 1e8).toFixed(0)}`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => formatMoney(v)}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Bar dataKey="revenue" name="营业收入" fill={COLORS.revenue} radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </FinanceChartCard>

      {/* 净利润 */}
      <FinanceChartCard title="净利润" unit="亿">
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${(v / 1e8).toFixed(0)}`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => formatMoney(v)}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Bar dataKey="net_profit" name="净利润" radius={[2, 2, 0, 0]}>
              {chartData.map((d, i) => (
                <Cell
                  key={`np-${i}`}
                  fill={d.net_profit >= 0 ? COLORS.netProfit : COLORS.netLoss}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </FinanceChartCard>

      {/* ROE + 净利率 */}
      <FinanceChartCard title="ROE & 净利率" unit="%">
        <ResponsiveContainer width="100%" height={140}>
          <LineChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${v.toFixed(0)}%`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => `${v.toFixed(2)}%`}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Line
              type="monotone"
              dataKey="roe"
              name="ROE"
              stroke={COLORS.roe}
              strokeWidth={2}
              dot={{ r: 2 }}
            />
            <Line
              type="monotone"
              dataKey="net_profit_margin"
              name="净利率"
              stroke={COLORS.margin}
              strokeWidth={2}
              dot={{ r: 2 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </FinanceChartCard>

      {/* 营收 & 净利同比增速 */}
      <FinanceChartCard title="同比增长" unit="%">
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${v.toFixed(0)}%`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => `${v.toFixed(2)}%`}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Bar dataKey="revenue_yoy" name="营收同比" fill="#60a5fa" radius={[2, 2, 0, 0]} />
            <Bar dataKey="net_profit_yoy" name="净利同比" fill="#34d399" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </FinanceChartCard>

      {/* 资产负债率 */}
      <FinanceChartCard title="资产负债率" unit="%">
        <ResponsiveContainer width="100%" height={120}>
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${v.toFixed(0)}%`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => `${v.toFixed(2)}%`}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Bar dataKey="debt_to_assets" name="资产负债率" fill="#94a3b8" radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </FinanceChartCard>

      {/* ── 现金流量 ── */}
      <div className="pt-2">
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          现金流量
        </h4>
      </div>

      {/* 经营活动现金流 */}
      <FinanceChartCard title="经营活动现金流量净额" unit="亿">
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${(v / 1e8).toFixed(0)}`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => formatMoney(v)}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Bar dataKey="ocf" name="经营活动现金流" radius={[2, 2, 0, 0]}>
              {chartData.map((d, i) => (
                <Cell
                  key={`ocf-${i}`}
                  fill={d.ocf >= 0 ? COLORS.ocf : COLORS.netLoss}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </FinanceChartCard>

      {/* 三大活动现金流 */}
      <FinanceChartCard title="三大活动现金流" unit="亿">
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${(v / 1e8).toFixed(0)}`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => formatMoney(v)}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Bar dataKey="ocf" name="经营" fill={COLORS.ocf} radius={[2, 2, 0, 0]} />
            <Bar dataKey="investing_cf" name="投资" fill={COLORS.investingCf} radius={[2, 2, 0, 0]} />
            <Bar dataKey="financing_cf" name="筹资" fill={COLORS.financingCf} radius={[2, 2, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </FinanceChartCard>

      {/* 自由现金流 */}
      <FinanceChartCard title="自由现金流 (OCF - CapEx)" unit="亿">
        <ResponsiveContainer width="100%" height={140}>
          <BarChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              interval="preserveStartEnd"
            />
            <YAxis
              tickFormatter={(v) => `${(v / 1e8).toFixed(0)}`}
              tick={{ fontSize: 10, fill: COLORS.textMuted }}
              width={40}
            />
            <Tooltip
              formatter={(v: number) => formatMoney(v)}
              labelStyle={{ fontSize: 11 }}
              contentStyle={{ fontSize: 11 }}
            />
            <Bar dataKey="fcf" name="自由现金流" radius={[2, 2, 0, 0]}>
              {chartData.map((d, i) => (
                <Cell
                  key={`fcf-${i}`}
                  fill={d.fcf >= 0 ? COLORS.fcfPositive : COLORS.fcfNegative}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </FinanceChartCard>
    </div>
  )
}

function FinanceChartCard({
  title,
  unit,
  children,
}: {
  title: string
  unit: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-md border border-border/50 p-3">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs font-medium text-foreground">{title}</span>
        <span className="text-[10px] text-muted-foreground">单位: {unit}</span>
      </div>
      {children}
    </div>
  )
}

function formatMoney(v: number): string {
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return `${v.toFixed(0)}`
}
