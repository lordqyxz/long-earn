import { useState, useEffect, useRef } from 'react'
import { Send, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PipelineStages } from '@/components/event-flow/PipelineStages'
import { useWebSocket, useEventData } from '@/hooks/useWebSocket'
import { formatDateTime } from '@/lib/utils'
import type { EventItem } from '@/api'

const SENTIMENT_MAP: Record<string, { label: string; variant: 'success' | 'destructive' | 'outline' | 'secondary' }> = {
  positive: { label: '利好', variant: 'success' },
  negative: { label: '利空', variant: 'destructive' },
  neutral: { label: '中性', variant: 'outline' },
}

export function EventFlowPage() {
  const { connected, log, pipelineStage, pipelineProgress, triggerPipeline, reloadData } = useWebSocket()
  const { stats, events, loading, error, reload } = useEventData()
  const [query, setQuery] = useState('近期降息政策对A股的影响')
  const [running, setRunning] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)

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
        {/* 触发区 */}
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm">触发事件推理管线</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex gap-2">
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="输入查询（如：近期降息政策对A股的影响）"
                className="flex-1 h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onKeyDown={(e) => e.key === 'Enter' && handleTrigger()}
              />
              <Button onClick={handleTrigger} disabled={running || !connected}>
                <Send className="h-4 w-4 mr-1" />
                {running ? '运行中...' : '运行推理管线'}
              </Button>
              <Button variant="outline" onClick={handleReload}>
                <RefreshCw className="h-4 w-4 mr-1" />
                刷新数据
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* 管线进度 */}
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm">推理管线进度</CardTitle>
          </CardHeader>
          <CardContent>
            <PipelineStages stage={pipelineStage} progress={pipelineProgress} />
          </CardContent>
        </Card>

        {/* 运行日志 */}
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm">运行日志</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="bg-zinc-950 text-zinc-300 rounded-md p-3 h-40 overflow-y-auto font-mono text-xs leading-relaxed">
              {log.length === 0 && <div className="text-muted-foreground/50">等待 WebSocket 连接...</div>}
              {log.map((line, i) => (
                <div key={i} className={line.includes('[error]') ? 'text-destructive' : line.includes('连接') ? 'text-success' : ''}>
                  {line}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          </CardContent>
        </Card>

        {/* 统计卡片 */}
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

        {/* 事件列表 */}
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm">事件列表</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[140px]">时间</TableHead>
                  <TableHead className="w-[60px]">情绪</TableHead>
                  <TableHead className="w-[100px]">标的</TableHead>
                  <TableHead className="w-[80px]">类别</TableHead>
                  <TableHead>内容</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                      {loading ? '加载中...' : '暂无事件数据'}
                    </TableCell>
                  </TableRow>
                ) : (
                  events.map((e: EventItem) => {
                    const sent = SENTIMENT_MAP[e.sentiment ?? 'neutral'] || SENTIMENT_MAP.neutral
                    return (
                      <TableRow key={e.sid}>
                        <TableCell className="text-xs text-muted-foreground">{formatDateTime(e.created_at ?? '')}</TableCell>
                        <TableCell>
                          <Badge variant={sent.variant} className="text-xs">{sent.label}</Badge>
                        </TableCell>
                        <TableCell className="font-mono text-xs">{e.symbols?.join(', ') || '-'}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{e.category || '-'}</TableCell>
                        <TableCell className="text-xs max-w-[400px] truncate">{e.content?.slice(0, 80)}</TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
    </div>
  )
}