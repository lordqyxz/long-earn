import { useMemo, useState } from 'react'
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Customized,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Brush,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { TrendingUp, Loader2, ArrowUp, ArrowDown } from 'lucide-react'
import {
  CHART_TRADE_LOOKBACK_DAYS_AFTER,
  CHART_TRADE_LOOKBACK_DAYS_BEFORE,
} from '@/lib/constants'
import type { SymbolChartData } from '@/api'
import {
  type CandleData,
  CANDLE_COLORS,
  CandleShape,
  KlineInfo,
  TradeTooltip,
  formatQty,
  renderTradeMarkers,
  renderVolumeBars,
} from '@/components/dashboard/charts/symbolChartRenderers'

interface Props {
  data: SymbolChartData | null
  loading: boolean
  symbolName?: string
}

export function SymbolChart({ data, loading, symbolName }: Props) {
  const [activeCandle, setActiveCandle] = useState<CandleData | null>(null)

  const { buyPoints, sellPoints } = useMemo(() => {
    if (!data) return { buyPoints: [], sellPoints: [] }
    return {
      buyPoints: (data.trade_points ?? []).filter((p) => p.direction === 'BUY'),
      sellPoints: (data.trade_points ?? []).filter((p) => p.direction === 'SELL'),
    }
  }, [data])

  const { totalBuyQty, totalSellQty } = useMemo(() => ({
    totalBuyQty: buyPoints.reduce((s, p) => s + (p.quantity ?? 0), 0),
    totalSellQty: sellPoints.reduce((s, p) => s + (p.quantity ?? 0), 0),
  }), [buyPoints, sellPoints])

  const chartData = useMemo(() => {
    if (!data || (data.price_history ?? []).length === 0) return [] as CandleData[]

    const tradeDates = (data.trade_points ?? [])
      .map((tp) => tp.time?.slice(0, 10) || '')
      .filter(Boolean)
      .sort()

    let startDate = ''
    let endDate = ''
    if (tradeDates.length > 0) {
      const minDate = new Date(tradeDates[0])
      minDate.setDate(minDate.getDate() - CHART_TRADE_LOOKBACK_DAYS_BEFORE)
      startDate = minDate.toISOString().slice(0, 10)
      const maxDate = new Date(tradeDates[tradeDates.length - 1])
      maxDate.setDate(maxDate.getDate() + CHART_TRADE_LOOKBACK_DAYS_AFTER)
      endDate = maxDate.toISOString().slice(0, 10)
    }

    return (data.price_history ?? [])
      .filter((p) => {
        if (!startDate) return true
        const d = p.date?.slice(0, 10) || ''
        return d >= startDate && d <= endDate
      })
      .map((p) => {
        const date = p.date?.slice(0, 10) || ''
        const dayBuys = buyPoints.filter((tp) => tp.time?.slice(0, 10) === date)
        const daySells = sellPoints.filter((tp) => tp.time?.slice(0, 10) === date)
        const low = p.low ?? 0
        const high = p.high ?? low
        return {
          date,
          open: p.open ?? null,
          high: p.high ?? null,
          low: p.low ?? null,
          close: p.close ?? null,
          volume: p.volume ?? 0,
          ohlcRange: [low, high] as [number, number],
          buyPrice: dayBuys.length > 0 ? (p.close ?? null) : null,
          sellPrice: daySells.length > 0 ? (p.close ?? null) : null,
          buyCount: dayBuys.length,
          sellCount: daySells.length,
          buyQuantity: dayBuys.reduce((s, tp) => s + (tp.quantity ?? 0), 0),
          sellQuantity: daySells.reduce((s, tp) => s + (tp.quantity ?? 0), 0),
          buyAmount: dayBuys.reduce((s, tp) => s + (tp.amount ?? 0), 0),
          sellAmount: daySells.reduce((s, tp) => s + (tp.amount ?? 0), 0),
          buyReasons: [...new Set(dayBuys.map((tp) => tp.reason || '').filter(Boolean))],
          sellReasons: [...new Set(daySells.map((tp) => tp.reason || '').filter(Boolean))],
        }
      })
  }, [data, buyPoints, sellPoints])

  const buyDates = useMemo(
    () => [...new Set(buyPoints.map((tp) => tp.time?.slice(0, 10) || ''))],
    [buyPoints],
  )
  const sellDates = useMemo(
    () => [...new Set(sellPoints.map((tp) => tp.time?.slice(0, 10) || ''))],
    [sellPoints],
  )

  const [yDomainMin, yDomainMax] = useMemo(() => {
    if (!chartData.length) return [0, 100]
    const lows = chartData.filter((d) => d.low != null).map((d) => d.low as number)
    const highs = chartData.filter((d) => d.high != null).map((d) => d.high as number)
    if (!lows.length || !highs.length) return [0, 100]
    const min = Math.min(...lows)
    const max = Math.max(...highs)
    const padding = (max - min) * 0.05
    return [min - padding, max + padding]
  }, [chartData])

  const maxVolume = useMemo(() => {
    if (!chartData.length) return 1
    return Math.max(...chartData.map((d) => d.volume), 1)
  }, [chartData])

  const TradeMarkersComponent = useMemo(
    () => renderTradeMarkers(chartData),
    [chartData],
  )

  const VolumeBarsComponent = useMemo(
    () => renderVolumeBars(chartData),
    [chartData],
  )

  const displayCandle = activeCandle ?? chartData[chartData.length - 1] ?? null
  const COLORS = CANDLE_COLORS

  if (loading) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">交易标的图表</CardTitle>
        </CardHeader>
        <CardContent className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin" style={{ color: COLORS.up }} />
        </CardContent>
      </Card>
    )
  }

  if (!data) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">交易标的图表</CardTitle>
        </CardHeader>
        <CardContent className="text-center py-8" style={{ color: COLORS.textMuted }}>
          请在左侧运行列表中选择一个标的
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between py-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <TrendingUp className="h-4 w-4" style={{ color: COLORS.up }} />
          <span>{symbolName || data.symbol}</span>
          {symbolName && <span className="font-mono text-muted-foreground font-normal text-xs">{data.symbol}</span>}
        </CardTitle>
        <div className="flex items-center gap-1.5">
          <span className="inline-flex items-center gap-1 rounded-full bg-success/15 px-2.5 py-0.5 text-xs font-medium text-success" title={`买入 ${buyPoints.length} 笔，共 ${formatQty(totalBuyQty)} 股`}>
            <ArrowUp className="h-3 w-3" />
            买入 {buyPoints.length} 笔 / {formatQty(totalBuyQty)}
          </span>
          <span className="inline-flex items-center gap-1 rounded-full bg-destructive/15 px-2.5 py-0.5 text-xs font-medium text-destructive" title={`卖出 ${sellPoints.length} 笔，共 ${formatQty(totalSellQty)} 股`}>
            <ArrowDown className="h-3 w-3" />
            卖出 {sellPoints.length} 笔 / {formatQty(totalSellQty)}
          </span>
        </div>
      </CardHeader>
      <CardContent className="relative">
        <ResponsiveContainer width="100%" height={460}>
          <ComposedChart
            data={chartData}
            margin={{ top: 10, right: 10, left: 0, bottom: 5 }}
            onMouseMove={(state) => {
              const payload = state?.activePayload?.[0]?.payload as CandleData | undefined
              if (payload) setActiveCandle(payload)
            }}
            onMouseLeave={() => setActiveCandle(null)}
          >
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.grid} />
            <XAxis
              dataKey="date"
              scale="band"
              tick={{ fontSize: 11, fill: COLORS.textMuted }}
              tickFormatter={(v) => v?.slice(5, 10) || ''}
              axisLine={{ stroke: COLORS.grid }}
              tickLine={{ stroke: COLORS.grid }}
            />
            <YAxis
              domain={[yDomainMin, yDomainMax]}
              tick={{ fontSize: 11, fill: COLORS.textMuted }}
              tickFormatter={(v) => `¥${(v ?? 0).toFixed(1)}`}
              axisLine={{ stroke: COLORS.grid }}
              tickLine={{ stroke: COLORS.grid }}
              width={55}
            />
            <YAxis
              yAxisId="volume"
              orientation="right"
              domain={[0, maxVolume * 3]}
              hide
            />
            <Tooltip
              content={<TradeTooltip />}
              cursor={{ stroke: COLORS.textMuted, strokeWidth: 1, strokeDasharray: '3 3', strokeOpacity: 0.3 }}
            />
            <Customized component={VolumeBarsComponent} />
            <Bar
              dataKey="ohlcRange"
              name="OHLC"
              shape={<CandleShape />}
              isAnimationActive={false}
            />
            <Customized component={TradeMarkersComponent} />
            {buyDates.map((d, i) => (
              <ReferenceLine
                key={`b-${i}`}
                x={d}
                stroke={COLORS.buy}
                strokeDasharray="2 2"
                strokeOpacity={0.15}
              />
            ))}
            {sellDates.map((d, i) => (
              <ReferenceLine
                key={`s-${i}`}
                x={d}
                stroke={COLORS.sell}
                strokeDasharray="2 2"
                strokeOpacity={0.15}
              />
            ))}
            <Brush
              dataKey="date"
              height={28}
              stroke={COLORS.brushStroke}
              fill={COLORS.brushFill}
              tickFormatter={(v) => v?.slice(5, 10) || ''}
            />
          </ComposedChart>
        </ResponsiveContainer>
        {displayCandle && <KlineInfo candle={displayCandle} />}
      </CardContent>
    </Card>
  )
}
