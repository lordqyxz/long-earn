import { Progress } from '@/components/ui/progress'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Loader2, CheckCircle2, XCircle, AlertTriangle, TrendingUp, RotateCw, Layers } from 'lucide-react'
import type { ResearchState } from '@/types/research'
import { NO_BEST_RETURN_SENTINEL } from '@/lib/constants'

interface Props {
  state: ResearchState
}

export function ResearchProgress({ state }: Props) {
  const { running, completed, connected, error, currentRound, maxRounds, familyIdx, stagnationCount, bestRecentReturn } = state

  // 进度百分比
  const progress = maxRounds > 0 ? Math.min(100, Math.round((currentRound / maxRounds) * 100)) : 0

  // 状态图标与标签
  let statusIcon: React.ReactNode
  let statusLabel: string
  let statusVariant: 'success' | 'destructive' | 'warning' | 'secondary' = 'secondary'

  if (!connected) {
    statusIcon = <XCircle className="h-4 w-4" />
    statusLabel = '未连接'
    statusVariant = 'destructive'
  } else if (error) {
    statusIcon = <AlertTriangle className="h-4 w-4" />
    statusLabel = '错误'
    statusVariant = 'destructive'
  } else if (completed) {
    statusIcon = <CheckCircle2 className="h-4 w-4" />
    statusLabel = '已完成'
    statusVariant = 'success'
  } else if (running) {
    statusIcon = <Loader2 className="h-4 w-4 animate-spin" />
    statusLabel = '运行中'
    statusVariant = 'success'
  } else {
    statusIcon = <CheckCircle2 className="h-4 w-4" />
    statusLabel = '就绪'
    statusVariant = 'secondary'
  }

  return (
    <Card>
      <CardContent className="p-4 space-y-4">
        {/* 状态栏 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Badge variant={statusVariant} className="gap-1">
              {statusIcon}
              {statusLabel}
            </Badge>
            {currentRound > 0 && (
              <span className="text-sm text-muted-foreground">
                第 {currentRound}/{maxRounds} 轮
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            {familyIdx > 0 && (
              <span className="flex items-center gap-1">
                <Layers className="h-3 w-3" />
                家族 #{familyIdx}
              </span>
            )}
            {stagnationCount > 0 && (
              <span className="flex items-center gap-1">
                <RotateCw className="h-3 w-3" />
                停滞 {stagnationCount}
              </span>
            )}
            {bestRecentReturn > NO_BEST_RETURN_SENTINEL && (
              <span className="flex items-center gap-1">
                <TrendingUp className="h-3 w-3" />
                最佳: {(bestRecentReturn * 100).toFixed(2)}%
              </span>
            )}
          </div>
        </div>

        {/* 进度条 */}
        {running && (
          <div className="space-y-1">
            <Progress value={progress} className="h-2" />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>轮次进度</span>
              <span>{progress}%</span>
            </div>
          </div>
        )}

        {/* 错误信息 */}
        {error && (
          <div className="p-3 bg-destructive/10 border border-destructive/30 rounded-md text-sm text-destructive">
            {error}
          </div>
        )}
      </CardContent>
    </Card>
  )
}