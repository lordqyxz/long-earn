import { Card, CardContent } from '@/components/ui/card'
import type { EventStats as EventStatsType } from '@/api'

interface EventStatsProps {
  stats: EventStatsType | null
  error: string | null
}

/** 事件统计概览卡片与加载错误提示 */
export function EventStats({ stats, error }: EventStatsProps) {
  return (
    <>
      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
          数据加载失败: {error}
        </div>
      )}
      <div className="grid grid-cols-4 gap-3">
        <Card className="hover:border-primary/30 transition-colors">
          <CardContent className="p-3">
            <div className="text-xs text-muted-foreground mb-1">事件总数</div>
            <div className="text-2xl font-bold">{stats?.total_events ?? '-'}</div>
          </CardContent>
        </Card>
        <Card className="hover:border-primary/30 transition-colors">
          <CardContent className="p-3">
            <div className="text-xs text-muted-foreground mb-1">影响关系</div>
            <div className="text-2xl font-bold">{stats?.total_relations ?? '-'}</div>
          </CardContent>
        </Card>
        <Card className="hover:border-primary/30 transition-colors">
          <CardContent className="p-3">
            <div className="text-xs text-muted-foreground mb-1">利好事件</div>
            <div className="text-2xl font-bold text-success">{stats?.by_sentiment?.positive ?? '-'}</div>
          </CardContent>
        </Card>
        <Card className="hover:border-primary/30 transition-colors">
          <CardContent className="p-3">
            <div className="text-xs text-muted-foreground mb-1">利空事件</div>
            <div className="text-2xl font-bold text-destructive">{stats?.by_sentiment?.negative ?? '-'}</div>
          </CardContent>
        </Card>
      </div>
    </>
  )
}
