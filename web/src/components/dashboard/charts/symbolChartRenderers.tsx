import type { ReactElement, FC } from 'react'
import { formatNumber } from '@/lib/utils'
import { CHART_COLORS } from '@/lib/chart-colors'

export function formatQty(qty: number): string {
  if (!qty || qty <= 0) return '0'
  if (qty >= 1e8) return `${(qty / 1e8).toFixed(2)}亿`
  if (qty >= 1e4) return `${(qty / 1e4).toFixed(2)}万`
  return `${qty.toFixed(0)}`
}

export const CANDLE_COLORS = {
  up: CHART_COLORS.up,
  down: CHART_COLORS.down,
  buy: CHART_COLORS.up,
  sell: CHART_COLORS.down,
  wickUp: '#22c55e',
  wickDown: '#ef4444',
  grid: CHART_COLORS.grid,
  tooltipBg: CHART_COLORS.tooltipBg,
  tooltipBorder: CHART_COLORS.tooltipBorder,
  brushFill: '#f1f5f9',
  brushStroke: '#94a3b8',
  textMuted: CHART_COLORS.textMuted,
  textBright: CHART_COLORS.textBright,
  volUp: CHART_COLORS.up,
  volDown: CHART_COLORS.down,
}

export interface CandleData {
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
  buyReasons: string[]
  sellReasons: string[]
}

export function CandleShape(props: {
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
  const color = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down
  const wickColor = isUp ? CANDLE_COLORS.wickUp : CANDLE_COLORS.wickDown

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

export function renderTradeMarkers(chartData: CandleData[]): FC<Record<string, unknown>> {
  const Component = (props: Record<string, unknown>): ReactElement | null => {
    const xAxisMap = props.xAxisMap as Record<string, { scale: { (val: string): number; bandwidth?: () => number } }> | undefined
    const yAxisMap = props.yAxisMap as Record<string, { scale: { (val: number): number } }> | undefined
    if (!xAxisMap || !yAxisMap) return null

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
              fill={CANDLE_COLORS.buy}
              stroke={CANDLE_COLORS.buy}
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
              fill={CANDLE_COLORS.sell}
              stroke={CANDLE_COLORS.sell}
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

export function renderVolumeBars(chartData: CandleData[]): FC<Record<string, unknown>> {
  const Component = (props: Record<string, unknown>): ReactElement | null => {
    const xAxisMap = props.xAxisMap as Record<string, { scale: { (val: string): number; bandwidth?: () => number } }> | undefined
    const yAxisMap = props.yAxisMap as Record<string, { scale: { (val: number): number } }> | undefined
    if (!xAxisMap || !yAxisMap) return null

    const xAxis = Object.values(xAxisMap)[0]
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
      const color = isUp ? CANDLE_COLORS.volUp : CANDLE_COLORS.volDown

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

export function TradeTooltip({ active, payload }: {
  active?: boolean
  payload?: Array<{ payload: CandleData }>
}) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p || (p.buyCount === 0 && p.sellCount === 0)) return null

  return (
    <div style={{
      background: CANDLE_COLORS.tooltipBg,
      border: `1px solid ${CANDLE_COLORS.tooltipBorder}`,
      borderRadius: '8px',
      padding: '8px 12px',
      fontSize: '12px',
      color: CANDLE_COLORS.textBright,
      boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      lineHeight: '1.6',
    }}>
      <div style={{ color: CANDLE_COLORS.textMuted, marginBottom: '4px', fontSize: '11px' }}>{p.date}</div>
      {p.buyCount > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: CANDLE_COLORS.buy, marginBottom: '2px' }}>
          <span>▲ 买入 {p.buyCount} 笔</span>
          <span style={{ color: CANDLE_COLORS.textMuted }}>|</span>
          <span>{formatQty(p.buyQuantity)} 股</span>
          <span style={{ color: CANDLE_COLORS.textMuted }}>|</span>
          <span>¥{formatNumber(p.buyAmount)}</span>
        </div>
      )}
      {(p.buyReasons ?? []).length > 0 && (
        <div style={{ color: CANDLE_COLORS.buy, fontSize: '11px', marginBottom: '2px' }}>
          {p.buyReasons.join('、')}
        </div>
      )}
      {p.sellCount > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: CANDLE_COLORS.sell }}>
          <span>▼ 卖出 {p.sellCount} 笔</span>
          <span style={{ color: CANDLE_COLORS.textMuted }}>|</span>
          <span>{formatQty(p.sellQuantity)} 股</span>
          <span style={{ color: CANDLE_COLORS.textMuted }}>|</span>
          <span>¥{formatNumber(p.sellAmount)}</span>
        </div>
      )}
      {(p.sellReasons ?? []).length > 0 && (
        <div style={{ color: CANDLE_COLORS.sell, fontSize: '11px' }}>
          {p.sellReasons.join('、')}
        </div>
      )}
    </div>
  )
}

export function KlineInfo({ candle }: { candle: CandleData }) {
  const openVal = candle.open ?? 0
  const closeVal = candle.close ?? 0
  const isUp = closeVal >= openVal
  const changePct = openVal !== 0
    ? ((closeVal - openVal) / openVal) * 100
    : 0
  const color = isUp ? CANDLE_COLORS.up : CANDLE_COLORS.down

  const labelStyle: React.CSSProperties = { color: CANDLE_COLORS.textMuted, marginRight: '2px' }
  const sepStyle: React.CSSProperties = { color: CANDLE_COLORS.grid, margin: '0 6px' }

  return (
    <div style={{
      position: 'absolute',
      left: '50%',
      transform: 'translateX(-50%)',
      top: '8px',
      background: 'rgba(255,255,255,0.92)',
      border: `1px solid ${CANDLE_COLORS.tooltipBorder}`,
      borderRadius: '6px',
      padding: '5px 10px',
      fontSize: '11px',
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
      color: CANDLE_COLORS.textBright,
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
      <span style={{ color: CANDLE_COLORS.up }}>{candle.high?.toFixed(2) ?? '-'}</span>
      <span style={sepStyle}>|</span>
      <span style={labelStyle}>低</span>
      <span style={{ color: CANDLE_COLORS.down }}>{candle.low?.toFixed(2) ?? '-'}</span>
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
