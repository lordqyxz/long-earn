import { useRef } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

interface EventLogProps {
  log: string[]
}

/** WebSocket 运行日志展示 */
export function EventLog({ log }: EventLogProps) {
  const logEndRef = useRef<HTMLDivElement>(null)

  return (
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
  )
}
