export interface RunInfo {
  run_id: string
  started: string
  total_return?: number
  sharpe?: number
  trade_count?: number
  event_count?: number
  status?: string
  strategy_id?: string
}

export interface MetricCard {
  label: string
  value: string
  sub?: string
  trend?: 'up' | 'down' | 'neutral'
}

export interface RiskMetrics {
  total_return: number
  annual_return: number
  annual_volatility: number
  sharpe_ratio: number
  max_drawdown: number
  max_drawdown_duration_days: number
  var_95: number
  var_99: number
  cvar_95: number
}

export interface Benchmark {
  alpha: number
  beta: number
  information_ratio: number
  tracking_error: number
}

export interface EquityPoint {
  time: string
  value: number
}

export interface TradePoint {
  time: string
  price: number
  action: 'BUY' | 'SELL'
  quantity: number
}

export interface TradeRecord {
  trace_id: string
  symbol: string
  type: string
  price: number
  quantity: number
  time: string
  portfolio_value: number
}

export interface SignalRecord {
  signal_id: string
  symbol: string
  action: string
  strength: number
  timestamp: string
}

export interface DashboardData {
  equity_curve: EquityPoint[]
  trade_journal: TradeRecord[]
  signal_history: SignalRecord[]
  event_breakdown: Record<string, number>
  benchmark: Benchmark
  risk_metrics: RiskMetrics
  traded_symbols: string[]
  total_events: number
  time_range: { start: string; end: string }
}

export interface SymbolChartData {
  symbol: string
  price_history: PricePoint[]
  trade_points: { time: string; price: number; direction: string; quantity: number; amount: number }[]
}

export interface PricePoint {
  date: string
  open: number | null
  high: number | null
  low: number | null
  close: number | null
  volume: number
}

export interface EventStats {
  total_events: number
  total_relations: number
  by_sentiment: Record<string, number>
  by_category: Record<string, number>
  top_symbols: [string, number][]
}

export interface EventItem {
  sid: string
  name: string
  content: string
  event_type: string
  category: string
  sentiment: string
  symbols: string[]
  created_at: string
}

export interface RelationItem {
  source_sid: string
  target_sid: string
  relation_type: string
  direction: string
  confidence: number
}

export interface TimelinePoint {
  date: string
  count: number
  positive: number
  negative: number
  neutral: number
}

export interface PipelineMessage {
  type: 'pipeline_start' | 'pipeline_progress' | 'pipeline_complete' | 'pipeline_error' | 'pong' | 'subscribed'
  query?: string
  stage?: string
  progress?: number
  status?: string
  detail?: string
  stats?: EventStats
  message?: string
}