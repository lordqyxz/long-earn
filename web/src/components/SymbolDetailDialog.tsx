import { useState, useEffect, useMemo } from 'react'
import { X, Building2, Loader2 } from 'lucide-react'
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

export interface SymbolDetail {
  symbol: string
  name: string
  industry: string
  region: string
  listing_date: string
  total_shares: number
  float_shares: number
  market_value: number
  flow_market_value: number
}

interface FinancialRecord {
  report_date: string
  announce_date: string
  revenue: number
  net_profit: number
  research_expenses: number
  eps: number
  bps: number
  roe: number
  roe_weighted: number
  gross_margin: number
  net_profit_margin: number
  net_profit_yoy: number
  revenue_yoy: number
  debt_to_assets: number
  ocf: number
  capex: number
  investing_cf: number
  financing_cf: number
  net_cash_change: number
  cash_from_sales: number
}

interface Props {
  symbol: string | null
  onClose: () => void
}

const API_BASE = '/api'

// ── 配色 ──
const COLORS = {
  revenue: '#3b82f6',
  netProfit: '#16a34a',
  netLoss: '#dc2626',
  roe: '#8b5cf6',
  margin: '#f59e0b',
  grid: '#e2e8f0',
  textMuted: '#64748b',
  textBright: '#1e293b',
  // 现金流配色
  ocf: '#16a34a',
  investingCf: '#3b82f6',
  financingCf: '#f59e0b',
  capex: '#94a3b8',
  fcfPositive: '#16a34a',
  fcfNegative: '#dc2626',
}

export function SymbolDetailDialog({ symbol, onClose }: Props) {
  const [detail, setDetail] = useState<SymbolDetail | null>(null)
  const [financials, setFinancials] = useState<FinancialRecord[]>([])
  const [loading, setLoading] = useState(false)
  const [loadingFin, setLoadingFin] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!symbol) {
      setDetail(null)
      setFinancials([])
      return
    }
    setLoading(true)
    setError(null)
    fetch(`${API_BASE}/symbols/${symbol}/detail`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json()
      })
      .then(setDetail)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))

    // 并行获取财务数据
    setLoadingFin(true)
    fetch(`${API_BASE}/symbols/${symbol}/financials`)
      .then((res) => {
        if (!res.ok) return { financials: [] }
        return res.json()
      })
      .then((data) => setFinancials(data.financials || []))
      .catch(() => setFinancials([]))
      .finally(() => setLoadingFin(false))
  }, [symbol])

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

  if (!symbol) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg border border-border bg-card p-0 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-3">
          <div className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-primary" />
            <span className="text-sm font-semibold">{detail?.name || symbol}</span>
            {detail?.name && (
              <span className="font-mono text-sm text-muted-foreground">{symbol}</span>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body — 可滚动 */}
        <div className="flex-1 overflow-y-auto p-5">
          {loading && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-primary" />
            </div>
          )}

          {error && (
            <div className="text-center text-sm text-destructive py-8">
              获取详情失败: {error}
            </div>
          )}

          {detail && !loading && !error && (
            <>
              {/* 公司信息 — 两列网格 */}
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                公司信息
              </h3>
              <div className="mb-6 grid grid-cols-2 gap-x-6 gap-y-2">
                <DetailRow label="公司名称" value={detail.name || '-'} />
                <DetailRow label="所属行业" value={detail.industry || '-'} />
                <DetailRow label="所在地区" value={detail.region || '-'} />
                <DetailRow label="上市日期" value={detail.listing_date || '-'} />
                <DetailRow
                  label="总股本"
                  value={formatShares(detail.total_shares)}
                />
                <DetailRow
                  label="流通股本"
                  value={formatShares(detail.float_shares)}
                />
                <DetailRow
                  label="总市值"
                  value={formatMarketValue(detail.market_value)}
                />
                <DetailRow
                  label="流通市值"
                  value={formatMarketValue(detail.flow_market_value)}
                />
              </div>

              {/* 历年财务数据可视化 */}
              <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                历年财务数据
              </h3>

              {loadingFin && (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                </div>
              )}

              {!loadingFin && chartData.length === 0 && (
                <div className="py-6 text-center text-sm text-muted-foreground">
                  暂无财务数据
                </div>
              )}

              {!loadingFin && chartData.length > 0 && (
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
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

// ── 子组件 ──

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border/50 pb-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-right">{value}</span>
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

// ── 格式化工具 ──

function formatShares(shares: number): string {
  if (!shares || shares <= 0) return '-'
  if (shares >= 1e8) return `${(shares / 1e8).toFixed(2)} 亿股`
  if (shares >= 1e4) return `${(shares / 1e4).toFixed(2)} 万股`
  return `${shares.toFixed(0)} 股`
}

function formatMarketValue(value: number): string {
  if (!value || value <= 0) return '-'
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)} 亿元`
  if (value >= 1e4) return `${(value / 1e4).toFixed(2)} 万元`
  return `${value.toFixed(0)} 元`
}

function formatMoney(v: number): string {
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return `${v.toFixed(0)}`
}
