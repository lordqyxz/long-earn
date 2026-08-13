import { useResearchWebSocket } from '@/hooks/useResearchWebSocket'
import { ResearchTrigger } from '@/components/research/ResearchTrigger'
import { ResearchProgress } from '@/components/research/ResearchProgress'
import { ResearchRounds } from '@/components/research/ResearchRounds'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { ScrollText, FlaskConical } from 'lucide-react'

export function ResearchPage() {
  const { state, startResearch, reset } = useResearchWebSocket()

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-primary" />
            策略研发
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            触发策略研究循环，实时追踪研发进度与中间状态
          </p>
        </div>
        {state.completed && (
          <Badge
            variant="outline"
            className="cursor-pointer hover:bg-muted"
            onClick={reset}
          >
            重置
          </Badge>
        )}
      </div>

      {/* 控制面板 */}
      <ResearchTrigger
        onStart={startResearch}
        running={state.running}
        connected={state.connected}
      />

      {/* 实时进度 */}
      <ResearchProgress state={state} />

      {/* 研究思路 */}
      {state.idea && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <ScrollText className="h-4 w-4" />
              当前研究思路
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">{state.idea}</p>
          </CardContent>
        </Card>
      )}

      {/* 轮次结果 */}
      <ResearchRounds rounds={state.rounds} running={state.running} />

      {/* 事件日志 */}
      {state.events.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <ScrollText className="h-4 w-4" />
              事件日志
              <Badge variant="secondary" className="ml-1">{state.events.length}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1.5 max-h-64 overflow-y-auto">
              {[...state.events].reverse().map((evt, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 text-xs py-1 px-2 rounded hover:bg-muted/50"
                >
                  <Badge
                    variant={evt.type === 'research_error' ? 'destructive' : 'secondary'}
                    className="text-[10px] px-1 py-0 shrink-0"
                  >
                    {evt.type.replace('research_', '')}
                  </Badge>
                  <span className="text-muted-foreground">
                    {evt.type === 'round_start' && `第 ${evt.round} 轮开始 (家族 ${evt.family_idx})`}
                    {evt.type === 'round_complete' && `第 ${evt.round} 轮完成 ${evt.improved ? '✓ 改善' : '✗ 无改善'}`}
                    {evt.type === 'family_switch' && `切换到家族 #${evt.family_idx}`}
                    {evt.type === 'research_complete' && '研究完成'}
                    {evt.type === 'research_error' && `错误: ${evt.detail}`}
                    {evt.type === 'research_started' && `已启动 (${evt.max_rounds} 轮, ${evt.max_iterations} 迭代)`}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}