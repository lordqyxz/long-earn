import { useMemo } from 'react'
import { TrendingUp, TrendingDown, Clock, FileText, MessageSquare } from 'lucide-react'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { RoundMetrics } from '@/types/research'

interface Props {
  rounds: RoundMetrics[]
  running: boolean
}

export function ResearchRounds({ rounds, running }: Props) {
  const sorted = useMemo(() => [...rounds].sort((a, b) => a.round - b.round), [rounds])

  // 找最佳轮次
  const bestRound = useMemo(() => {
    if (sorted.length === 0) return null
    return sorted.reduce((best, r) => (r.recent_return > best.recent_return ? r : best), sorted[0])
  }, [sorted])

  if (sorted.length === 0 && !running) {
    return (
      <Card>
        <CardHeader className="py-3">
          <CardTitle className="text-sm">研究轮次</CardTitle>
        </CardHeader>
        <CardContent className="text-center text-muted-foreground py-8">
          尚未开始策略研究
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <TrendingUp className="h-4 w-4" />
          研究轮次
          <Badge variant="secondary" className="ml-1">{sorted.length}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[60px]">轮次</TableHead>
              <TableHead className="text-right w-[90px]">近三月收益</TableHead>
              <TableHead className="text-right w-[70px]">夏普</TableHead>
              <TableHead className="text-right w-[80px]">最大回撤</TableHead>
              <TableHead className="text-right w-[90px]">历史收益</TableHead>
              <TableHead className="text-right w-[70px]">耗时</TableHead>
              <TableHead className="w-[80px]">策略</TableHead>
              <TableHead className="w-[80px]">反思</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((r) => {
              const isBest = r.round === bestRound?.round
              return (
                <TableRow key={r.round} className={isBest ? 'bg-primary/5' : ''}>
                  <TableCell>
                    <div className="flex items-center gap-1.5">
                      <span className="font-mono text-xs">{r.round}</span>
                      {isBest && <Badge variant="success" className="text-[10px] px-1 py-0">最佳</Badge>}
                    </div>
                  </TableCell>
                  <TableCell className="text-right">
                    <span className={r.recent_return >= 0 ? 'text-success' : 'text-destructive'}>
                      {(r.recent_return * 100).toFixed(2)}%
                    </span>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs">
                    {r.recent_sharpe.toFixed(2)}
                  </TableCell>
                  <TableCell className="text-right text-destructive font-mono text-xs">
                    {(r.recent_drawdown * 100).toFixed(2)}%
                  </TableCell>
                  <TableCell className="text-right">
                    <span className={r.history_return >= 0 ? 'text-success' : 'text-destructive'}>
                      {(r.history_return * 100).toFixed(2)}%
                    </span>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs text-muted-foreground">
                    {r.elapsed.toFixed(1)}s
                  </TableCell>
                  <TableCell>
                    {r.strategy_yaml ? (
                      <button
                        className="text-xs text-primary hover:underline flex items-center gap-1"
                        onClick={() => {
                          const w = window.open('', '_blank')
                          if (w) {
                            w.document.write(`<pre style="padding:16px;background:#f8fafc;color:#1e293b;border:1px solid #e2e8f0;border-radius:8px;font-size:12px;white-space:pre-wrap">${escapeHtml(r.strategy_yaml)}</pre>`)
                          }
                        }}
                      >
                        <FileText className="h-3 w-3" />
                        查看
                      </button>
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
                  </TableCell>
                  <TableCell>
                    {r.reflection ? (
                      <button
                        className="text-xs text-primary hover:underline flex items-center gap-1"
                        onClick={() => {
                          const w = window.open('', '_blank')
                          if (w) {
                            w.document.write(`<pre style="padding:16px;background:#f8fafc;color:#1e293b;border:1px solid #e2e8f0;border-radius:8px;font-size:12px;white-space:pre-wrap;max-width:600px">${escapeHtml(r.reflection)}</pre>`)
                          }
                        }}
                      >
                        <MessageSquare className="h-3 w-3" />
                        查看
                      </button>
                    ) : (
                      <span className="text-xs text-muted-foreground">-</span>
                    )}
                  </TableCell>
                </TableRow>
              )
            })}
            {/* 运行中占位行 */}
            {running && sorted.length === 0 && (
              <TableRow>
                <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                  <Clock className="h-4 w-4 inline-block animate-spin mr-2" />
                  等待首轮结果...
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}