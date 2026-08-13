import { useState } from 'react'
import { Play, Settings } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

const PRESET_IDEAS = [
  { label: '默认', value: '研究一个基于净利润增长和ROE的选股策略，要求近三个月收益率最大化' },
  { label: '动量', value: '研究一个基于20日价格动量的选股策略，选近20日收益率最高的股票，要求近六个月收益率最大化' },
  { label: '均值回归', value: '研究一个均值回归选股策略，选择近期跌幅过大、偏离20日均线较远但基本面稳健的股票，用 RSI 超卖信号过滤，要求近六个月收益率最大化' },
  { label: '价值成长', value: '研究一个价值成长选股策略，选择 ROE>0.12 且净利润同比增长>20% 且毛利率稳定的股票，要求近六个月收益率最大化' },
  { label: '多因子', value: '研究一个多因子复合选股策略，结合动量、低波动率、高ROE和成交量放大，用算子路径实现滚动窗口因子，要求近六个月收益率最大化' },
]

interface Props {
  onStart: (idea: string, maxRounds: number, maxIterations: number, minImprovement: number) => void
  running: boolean
  connected: boolean
}

export function ResearchTrigger({ onStart, running, connected }: Props) {
  const [idea, setIdea] = useState(PRESET_IDEAS[0].value)
  const [maxRounds, setMaxRounds] = useState(3)
  const [maxIterations, setMaxIterations] = useState(2)
  const [minImprovement, setMinImprovement] = useState(0.005)
  const [showAdvanced, setShowAdvanced] = useState(false)

  const handleStart = () => {
    if (!idea.trim()) return
    onStart(idea.trim(), maxRounds, maxIterations, minImprovement)
  }

  return (
    <Card>
      <CardHeader className="py-3">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Play className="h-4 w-4" />
          策略研究
          {!connected && (
            <Badge variant="destructive" className="text-xs">未连接</Badge>
          )}
          {running && (
            <Badge variant="success" className="text-xs animate-pulse">运行中</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {/* 预设思路 */}
        <div className="flex flex-wrap gap-1.5">
          {PRESET_IDEAS.map((p) => (
            <button
              key={p.label}
              onClick={() => setIdea(p.value)}
              className={cn(
                'px-2.5 py-1 rounded text-xs transition-colors',
                idea === p.value
                  ? 'bg-primary/20 text-primary font-medium'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
              )}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* 思路输入 */}
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          disabled={running}
          rows={3}
          className="w-full bg-muted/50 border border-border rounded-md px-3 py-2 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50"
          placeholder="输入策略研究思路..."
        />

        {/* 高级设置 */}
        <div className="flex items-center justify-between">
          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <Settings className="h-3 w-3" />
            高级设置
          </button>
          <Button
            onClick={handleStart}
            disabled={running || !connected || !idea.trim()}
            size="sm"
            className="gap-1.5"
          >
            <Play className="h-3.5 w-3.5" />
            开始研究
          </Button>
        </div>

        {showAdvanced && (
          <div className="grid grid-cols-3 gap-3 p-3 bg-muted/30 rounded-md">
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">最大轮次</label>
              <input
                type="number"
                value={maxRounds}
                onChange={(e) => setMaxRounds(Math.max(1, Math.min(10, Number(e.target.value))))}
                disabled={running}
                min={1}
                max={10}
                className="w-full bg-background border border-border rounded px-2 py-1 text-sm text-center"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">子图迭代</label>
              <input
                type="number"
                value={maxIterations}
                onChange={(e) => setMaxIterations(Math.max(1, Math.min(5, Number(e.target.value))))}
                disabled={running}
                min={1}
                max={5}
                className="w-full bg-background border border-border rounded px-2 py-1 text-sm text-center"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">改善阈值</label>
              <input
                type="number"
                value={minImprovement}
                onChange={(e) => setMinImprovement(Math.max(0.001, Math.min(0.1, Number(e.target.value))))}
                disabled={running}
                min={0.001}
                max={0.1}
                step={0.001}
                className="w-full bg-background border border-border rounded px-2 py-1 text-sm text-center"
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}