import { useState, useEffect } from 'react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { PipelineStages } from '@/components/event-flow/PipelineStages'
import { EventTrigger } from '@/components/event-flow/EventTrigger'
import { EventStats } from '@/components/event-flow/EventStats'
import { EventTable } from '@/components/event-flow/EventTable'
import { EventLog } from '@/components/event-flow/EventLog'
import { useEventPipelineWs } from '@/hooks/useEventPipelineWs'
import { useEventData } from '@/hooks/useEventData'

export function EventFlowPage() {
  const { connected, log, pipelineStage, pipelineProgress, triggerPipeline, reloadData } = useEventPipelineWs()
  const { stats, events, loading, error, reload } = useEventData()
  const [query, setQuery] = useState('近期降息政策对A股的影响')
  const [running, setRunning] = useState(false)

  // 管线终态由 WebSocket 推送的 pipelineStage 驱动（pipeline_complete → 'done'、pipeline_error → 'error'），
  // 进入终态时刷新数据并复位运行态，替代原固定 4 秒定时器。
  useEffect(() => {
    if (pipelineStage === 'done' || pipelineStage === 'error') {
      setRunning(false)
      reload()
    }
  }, [pipelineStage, reload])

  const handleTrigger = () => {
    if (!query.trim()) return
    setRunning(true)
    triggerPipeline(query.trim())
  }

  const handleReload = () => {
    reloadData()
    reload()
  }

  return (
    <div className="p-4 space-y-4">
      {/* 顶部状态栏 */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">事件流可视化</h1>
        <Badge variant={connected ? 'success' : 'destructive'} className="text-xs">
          {connected ? '已连接' : '连接中...'}
        </Badge>
      </div>

      <EventTrigger
        query={query}
        onQueryChange={setQuery}
        running={running}
        connected={connected}
        onTrigger={handleTrigger}
        onReload={handleReload}
      />

      {/* 管线进度 */}
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">推理管线进度</CardTitle>
        </CardHeader>
        <CardContent>
          <PipelineStages stage={pipelineStage} progress={pipelineProgress} />
        </CardContent>
      </Card>

      <EventLog log={log} />

      <EventStats stats={stats} error={error} />

      <EventTable events={events} loading={loading} />
    </div>
  )
}
