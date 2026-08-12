export interface ResearchEvent {
  type: 'round_start' | 'round_complete' | 'family_switch' | 'research_complete' | 'research_error' | 'research_started'
  round?: number
  total_rounds?: number
  family_idx?: number
  total_families?: number
  idea?: string
  improved?: boolean
  metrics?: RoundMetrics
  best_recent_return?: number
  stagnation_count?: number
  best_round?: number
  best_history_return?: number
  total_rounds_completed?: number
  detail?: string
  max_rounds?: number
  max_iterations?: number
}

export interface RoundMetrics {
  round: number
  recent_return: number
  recent_sharpe: number
  recent_drawdown: number
  history_return: number
  strategy_yaml: string
  reflection: string
  elapsed: number
  status?: string
}

export interface ResearchState {
  connected: boolean
  running: boolean
  idea: string
  maxRounds: number
  currentRound: number
  events: ResearchEvent[]
  rounds: RoundMetrics[]
  bestRecentReturn: number
  stagnationCount: number
  familyIdx: number
  completed: boolean
  error: string | null
}