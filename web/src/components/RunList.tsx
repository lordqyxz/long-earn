import { RefreshCw, TrendingUp, TrendingDown, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatShortDateTime, formatPercent } from '@/lib/utils'
import type { RunInfo } from '@/types'

interface Props {
  runs: RunInfo[]
  loading: boolean
  selectedRunId: string | null
  onSelect: (runId: string) => void
  onRefresh: () => void
  onDelete?: (runId: string) => void
}

export function RunList({ runs, loading, selectedRunId, onSelect, onRefresh, onDelete }: Props) {
  const handleDelete = (runId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (!onDelete) return
    if (window.confirm('确定删除此回测记录？此操作不可撤销。')) {
      onDelete(runId)
    }
  }

  return (
    <Card className="h-full flex flex-col rounded-none border-0 border-r">
      <CardHeader className="flex flex-row items-center justify-between py-3 shrink-0 border-b">
        <CardTitle className="flex items-center gap-2 text-sm">
          回测运行
          <Badge variant="secondary" className="ml-1 text-xs">{runs.length}</Badge>
        </CardTitle>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onRefresh} disabled={loading}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
        </Button>
      </CardHeader>
      <CardContent className="flex-1 overflow-auto p-0">
        {runs.length === 0 && (
          <div className="text-center text-muted-foreground py-8 text-sm">
            {loading ? '加载中...' : '暂无回测运行记录'}
          </div>
        )}
        {runs.map((run) => {
          const isSelected = run.run_id === selectedRunId
          const ret = run.total_return ?? 0
          const sharpe = run.sharpe ?? 0
          const isPositive = ret >= 0
          return (
            <div
              key={run.run_id}
              onClick={() => onSelect(run.run_id)}
              className={`group px-3 py-2 cursor-pointer border-b border-border transition-colors ${
                isSelected ? 'bg-primary/10' : 'hover:bg-muted/50'
              }`}
            >
              <div className="flex items-center justify-between mb-0.5">
                <span className={`text-xs font-semibold truncate ${isSelected ? 'text-primary' : 'text-foreground'}`}>
                  {run.strategy_id || '未知策略'}
                </span>
                <div className="flex items-center gap-1 shrink-0 ml-2">
                  {onDelete && (
                    <button
                      onClick={(e) => handleDelete(run.run_id, e)}
                      className="p-0.5 rounded text-muted-foreground/40 hover:text-destructive hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-all"
                      title="删除此回测"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  )}
                  <span className={`inline-flex items-center gap-0.5 text-xs font-semibold px-1.5 py-0.5 rounded ${
                    isPositive
                      ? 'bg-success/10 text-success'
                      : 'bg-destructive/10 text-destructive'
                  }`}>
                    {isPositive ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
                    {formatPercent(ret, 1)}
                  </span>
                </div>
              </div>
              <div className="flex items-center justify-between text-[11px] text-muted-foreground">
                <span>{formatShortDateTime(run.started)}</span>
                <span className="font-mono">SH {sharpe.toFixed(2)}</span>
              </div>
            </div>
          )
        })}
      </CardContent>
    </Card>
  )
}
