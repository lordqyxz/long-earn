import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import type { EventItem } from '@/api'
import { formatDateTime } from '@/lib/utils'

const SENTIMENT_MAP: Record<string, { label: string; variant: 'success' | 'destructive' | 'outline' | 'secondary' }> = {
  positive: { label: '利好', variant: 'success' },
  negative: { label: '利空', variant: 'destructive' },
  neutral: { label: '中性', variant: 'outline' },
}

interface EventTableProps {
  events: EventItem[]
  loading: boolean
}

/** 事件列表表格 */
export function EventTable({ events, loading }: EventTableProps) {
  return (
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
              events.map((e) => {
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
  )
}
