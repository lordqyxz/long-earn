import { useMemo, useState } from 'react'
import type { ReactElement, FC } from 'react'
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
import { formatNumber } from '@/lib/utils'
import type { SymbolChartData } from '@/api'

// ── 数量格式化：万/亿 ──
function formatQty(qty: number): string {
  if (!qty || qty <= 0) return '0'
  if (qty >= 1e8) return `${(qty / 1e8).toFixed(2)}亿`
  if (qty >= 1e4) return `${(qty / 1e4).toFixed(2)}万`
  return `${qty.toFixed(0)}`
}

interface Props {
  data: SymbolChartData | null
  loading: boolean
  symbolName?: string
}

// ── 配色方案：亮色金融仪表盘 ──
const COLORS = {
  up: '#16a34a',
  down: '#dc2626',
  buy: '#16a34a',
  sell: '#dc2626',
  wickUp: '#22c55e',
  wickDown: '#ef4444',
  grid: '#e2e8f0',
  tooltipBg: '#ffffff',
  tooltipBorder: '#e2e8f0',
  brushFill: '#f1f5f9',
  brushStroke: '#94a3b8',
  textMuted: '#64748b',
  textBright: '#1e293b',
  volUp: '#16a34a',
  volDown: '#dc2626',
}

// ── K 线数据接口 ──
interface CandleData {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number
  ohlcRange: [number, number]
  buyPrice: number | null
  sellPrice: number | null
  buyCount: number
  sellCount: number
  buyQuantity: number
  sellQuantity: number
  buyAmount: number
  sellAmount: number
}

// ── 自定义 K 线 Shape ──
function CandleShape(props: {
  x?: number
  y?: number
  width?: number
  height?: number
  payload?: CandleData
}) {
  const { x = 0, y = 0, width = 8, height = 0, payload } = props
  if (!payload || payload.open == null || payload.close == null || payload.high == null || payload.low == null) {
    return null
  }

  const { open, close, high, low } = payload
  const range = high - low
  const pxPerUnit = range > 0 ? height / range : 0

  const yHigh = y
  const yLow = y + height
  const yOpen = y + (high - open) * pxPerUnit
  const yClose = y + (high - close) * pxPerUnit

  const isUp = close >= open
  const color = isUp ? COLORS.up : COLORS.down
  const wickColor = isUp ? COLORS.wickUp : COLORS.wickDown

  const cx = x + width / 2
  const bodyWidth = Math.max(width * 0.6, 2)
  const bodyX = x + (width - bodyWidth) / 2
  const bodyTop = Math.min(yOpen, yClose)
  const bodyHeight = Math.max(Math.abs(yClose - yOpen), 1)

  return (
    <g>
      <line x1={cx} y1={yHigh} x2={cx} y2={yLow} stroke={wickColor} strokeWidth={1} />
      <rect
        x={bodyX}
        y={bodyTop}
        width={bodyWidth}
        height={bodyHeight}
        fill={color}
        stroke={color}
        strokeWidth={0.5}
        rx={0.5}
      />
    </g>
  )
}

// ── 买卖标志渲染器 ──
function renderTradeMarkers(chartData: CandleData[]): FC<Record<string, unknown>> {
  const Component = (props: Record<string, unknown>): ReactElement | null => {
    const xAxisMap = props.xAxisMap as Record<string, { scale: { (val: string): number; bandwidth?: () => number } }> | undefined
    const yAxisMap = props.yAxisMap as Record<string, { scale: { (val: number): number } }> | undefined
    if (!xAxisMap || !yAxisMap) return null

    // 优先取 yAxisId=0（价格轴），有 volume 轴时确保不会误取
    const xAxis = Object.values(xAxisMap)[0]
    const yAxis = yAxisMap['0'] ?? Object.values(yAxisMap)[0]
    if (!xAxis?.scale || !yAxis?.scale) return null

    const xScale = xAxis.scale
    const yScale = yAxis.scale
    const bandwidth = typeof xScale.bandwidth === 'function' ? xScale.bandwidth() : 0

    const markers: ReactElement[] = []
    chartData.forEach((d, i) => {
      const xPos = xScale(d.date)
      if (typeof xPos !== 'number' || isNaN(xPos)) return
      const cx = xPos + bandwidth / 2

      if (d.buyPrice != null) {
        const cy = yScale(d.buyPrice)
        if (typeof cy === 'number' && !isNaN(cy)) {
          markers.push(
            <polygon
              key={`buy-${i}`}
              points={`${cx - 6},${cy + 12} ${cx + 6},${cy + 12} ${cx},${cy + 2}`}
              fill={COLORS.buy}
              stroke={COLORS.buy}
              strokeWidth={1}
            />,
          )
        }
      }
      if (d.sellPrice != null) {
        const cy = yScale(d.sellPrice)
        if (typeof cy === 'number' && !isNaN(cy)) {
          markers.push(
            <polygon
              key={`sell-${i}`}
              points={`${cx - 6},${cy - 12} ${cx + 6},${cy - 12} ${cx},${cy - 2}`}
              fill={COLORS.sell}
              stroke={COLORS.sell}
              strokeWidth={1}
            />,
          )
        }
      }
    })

    return <g>{markers}</g>
  }
  return Component
}

// ── 成交量柱状图渲染器（与 K 线共享 X 轴确保对齐） ──
function renderVolumeBars(chartData: CandleData[]): FC<Record<string, unknown>> {
  const Component = (props: Record<string, unknown>): ReactElement | null => {
    const xAxisMap = props.xAxisMap as Record<string, { scale: { (val: string): number; bandwidth?: () => number } }> | undefined
    const yAxisMap = props.yAxisMap as Record<string, { scale: { (val: number): number } }> | undefined
    if (!xAxisMap || !yAxisMap) return null

    const xAxis = Object.values(xAxisMap)[0]
    // 取成交量轴（yAxisId="volume"）
    const volAxis = yAxisMap['volume']
    if (!xAxis?.scale || !volAxis?.scale) return null

    const xScale = xAxis.scale
    const yScale = volAxis.scale
    const bandwidth = typeof xScale.bandwidth === 'function' ? xScale.bandwidth() : 0
    const barWidth = Math.max(bandwidth * 0.6, 2)

    const bars: ReactElement[] = []
    chartData.forEach((d, i) => {
      if (d.volume <= 0) return
      const xPos = xScale(d.date)
      if (typeof xPos !== 'number' || isNaN(xPos)) return

      const cx = xPos + bandwidth / 2
      const yTop = yScale(d.volume)
      const yBottom = yScale(0)
      const barHeight = Math.max(yBottom - yTop, 0)
      if (barHeight < 1) return

      const isUp = (d.close ?? 0) >= (d.open ?? 0)
      const color = isUp ? COLORS.volUp : COLORS.volDown

      bars.push(
        <rect
          key={`vol-${i}`}
          x={cx - barWidth / 2}
          y={yTop}
          width={barWidth}
          height={barHeight}
          fill={color}
          fillOpacity={0.25}
        />
      )
    })

    return <g>{bars}</g>
  }
  return Component
}

// ── 自定义 Tooltip：仅显示交易信息 ──
function TradeTooltip({ active, payload }: {
  active?: boolean
  payload?: Array<{ payload: CandleData }>
}) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p || (p.buyCount === 0 && p.sellCount === 0)) return null

  return (
    <div style={{
      background: COLORS.tooltipBg,
      border: `1px solid ${COLORS.tooltipBorder}`,
      borderRadius: '8px',
      padding: '8px 12px',
      fontSize: '12px',
      color: COLORS.textBright,
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      lineHeight: '1.6',
    }}>
      <div style={{ color: COLORS.textMuted, marginBottom: '4px', fontSize: '11px' }}>{p.date}</div>
      {p.buyCount > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: COLORS.buy, marginBottom: '2px' }}>
          <span>▲ 买入 {p.buyCount} 笔</span>
          <span style={{ color: COLORS.textMuted }}>|</span>
          <span>{formatQty(p.buyQuantity)} 股</span>
          <span style={{ color: COLORS.textMuted }}>|</span>
          <span>¥{formatNumber(p.buyAmount)}</span>
        </div>
      )}
      {p.sellCount > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: COLORS.sell }}>
          <span>▼ 卖出 {p.sellCount} 笔</span>
          <span style={{ color: COLORS.textMuted }}>|</span>
          <span>{formatQty(p.sellQuantity)} 股</span>
          <span style={{ color: COLORS.textMuted }}>|</span>
          <span>¥{formatNumber(p.sellAmount)}</span>
        </div>
      )}
    </div>
  )
}

// ── K 线详情面板（左上角固定显示） ──
function KlineInfo({ candle }: { candle: CandleData }) {
  const openVal = candle.open ?? 0
  const closeVal = candle.close ?? 0
  const isUp = closeVal >= openVal
  const changePct = openVal !== 0
    ? ((closeVal - openVal) / openVal) * 100
    : 0
  const color = isUp ? COLORS.up : COLORS.down

  const labelStyle: React.CSSProperties = { color: COLORS.textMuted, marginRight: '2px' }
  const sepStyle: React.CSSProperties = { color: COLORS.grid, margin: '0 6px' }

  return (
    <div style={{
      position: 'absolute',
      left: '50%',
      transform: 'translateX(-50%)',
      top: '8px',
      background: 'rgba(255,255,255,0.92)',
      border: `1px solid ${COLORS.tooltipBorder}`,
      borderRadius: '6px',
      padding: '5px 10px',
      fontSize: '11px',
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      color: COLORS.textBright,
      pointerEvents: 'none',
      zIndex: 10,
      boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
      whiteSpace: 'nowrap',
    }}>
      <span style={labelStyle}>{candle.date}</span>
      <span style={sepStyle}>|</span>
      <span style={labelStyle}>开</span>
      <span>{candle.open?.toFixed(2) ?? '-'}</span>
      <span style={sepStyle}>|</span>
      <span style={labelStyle}>高</span>
      <span style={{ color: COLORS.up }}>{candle.high?.toFixed(2) ?? '-'}</span>
      <span style={sepStyle}>|</span>
      <span style={labelStyle}>低</span>
      <span style={{ color: COLORS.down }}>{candle.low?.toFixed(2) ?? '-'}</span>
      <span style={sepStyle}>|</span>
      <span style={labelStyle}>收</span>
      <span style={{ color }}>{candle.close?.toFixed(2) ?? '-'}</span>
      <span style={sepStyle}>|</span>
      <span style={{ color, fontWeight: 600 }}>
        {isUp ? '+' : ''}{changePct.toFixed(2)}%
      </span>
      <span style={sepStyle}>|</span>
      <span style={labelStyle}>量</span>
      <span>{formatQty(candle.volume)}</span>
    </div>
  )
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
      minDate.setDate(minDate.getDate() - 90)
      startDate = minDate.toISOString().slice(0, 10)
      const maxDate = new Date(tradeDates[tradeDates.length - 1])
      maxDate.setDate(maxDate.getDate() + 30)
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

  // 成交量最大值用于设定 volume 轴 domain（底部 ~30% 区域显示量柱）
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

  // 鼠标悬停的 K 线，默认显示最后一天
  const displayCandle = activeCandle ?? chartData[chartData.length - 1] ?? null

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
            {/* 价格 Y 轴（左侧） */}
            <YAxis
              domain={[yDomainMin, yDomainMax]}
              tick={{ fontSize: 11, fill: COLORS.textMuted }}
              tickFormatter={(v) => `¥${(v ?? 0).toFixed(1)}`}
              axisLine={{ stroke: COLORS.grid }}
              tickLine={{ stroke: COLORS.grid }}
              width={55}
            />
            {/* 成交量 Y 轴（右侧隐藏，domain 放大让量柱只占底部 ~30%） */}
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
            {/* 成交量柱状图（Customized 渲染，与 K 线共享 X 轴坐标确保对齐） */}
            <Customized component={VolumeBarsComponent} />
            {/* K 线蜡烛图 */}
            <Bar
              dataKey="ohlcRange"
              name="OHLC"
              shape={<CandleShape />}
              isAnimationActive={false}
            />
            {/* 买卖标志 */}
            <Customized component={TradeMarkersComponent} />
            {/* 买卖点垂直参考线 */}
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
            {/* 滑动缩放栏 */}
            <Brush
              dataKey="date"
              height={28}
              stroke={COLORS.brushStroke}
              fill={COLORS.brushFill}
              tickFormatter={(v) => v?.slice(5, 10) || ''}
            />
          </ComposedChart>
        </ResponsiveContainer>
        {/* K 线详情面板（左下角） */}
        {displayCandle && <KlineInfo candle={displayCandle} />}
      </CardContent>
    </Card>
  )
}
