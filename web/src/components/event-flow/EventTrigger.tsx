import { Send, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface EventTriggerProps {
  query: string
  onQueryChange: (value: string) => void
  running: boolean
  connected: boolean
  onTrigger: () => void
  onReload: () => void
}

/** 查询输入与管线触发、数据刷新操作区 */
export function EventTrigger({
  query,
  onQueryChange,
  running,
  connected,
  onTrigger,
  onReload,
}: EventTriggerProps) {
  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="text-sm">触发事件推理管线</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="输入查询（如：近期降息政策对A股的影响）"
            className="flex-1 h-10 rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onKeyDown={(e) => e.key === 'Enter' && onTrigger()}
          />
          <Button onClick={onTrigger} disabled={running || !connected}>
            <Send className="h-4 w-4 mr-1" />
            {running ? '运行中...' : '运行推理管线'}
          </Button>
          <Button variant="outline" onClick={onReload}>
            <RefreshCw className="h-4 w-4 mr-1" />
            刷新数据
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
