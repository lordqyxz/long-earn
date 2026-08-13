import { cn } from '@/lib/utils'

const STAGES = [
  { key: 'collect', label: '采集', icon: '1' },
  { key: 'extract', label: '提取', icon: '2' },
  { key: 'propagate', label: '传播', icon: '3' },
  { key: 'conflict', label: '冲突检测', icon: '4' },
  { key: 'save', label: '保存', icon: '5' },
]

interface Props {
  stage: string
  progress: number
}

export function PipelineStages({ stage, progress }: Props) {
  const stageIndex = STAGES.findIndex((s) => s.key === stage)
  const isDone = stage === 'done'
  const isError = stage === 'error'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        {STAGES.map((s, i) => {
          const isActive = isDone || i <= stageIndex
          const isCurrent = !isDone && !isError && i === stageIndex
          const isFailed = isError && i === stageIndex

          return (
            <div key={s.key} className="flex items-center flex-1 last:flex-none">
              <div className="flex flex-col items-center">
                <div
                  className={cn(
                    'w-9 h-9 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-all duration-300',
                    isFailed && 'border-destructive bg-destructive/20 text-destructive',
                    isCurrent && 'border-primary bg-primary/20 text-primary animate-pulse',
                    isActive && !isCurrent && !isFailed && 'border-success bg-success/20 text-success',
                    !isActive && !isFailed && 'border-muted-foreground/30 text-muted-foreground'
                  )}
                >
                  {isActive && !isCurrent && !isFailed ? '✓' : s.icon}
                </div>
                <span className={cn(
                  'text-xs mt-1.5',
                  isCurrent ? 'text-primary font-medium' : isActive ? 'text-success' : isFailed ? 'text-destructive' : 'text-muted-foreground'
                )}>
                  {s.label}
                </span>
              </div>
              {i < STAGES.length - 1 && (
                <div className={cn(
                  'flex-1 h-0.5 mx-1 mt-[-12px] transition-all duration-500',
                  isActive && i < stageIndex ? 'bg-success' : 'bg-muted-foreground/20'
                )} />
              )}
            </div>
          )
        })}
      </div>
      {/* Progress bar */}
      <div className="w-full h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            isError ? 'bg-destructive' : 'bg-primary'
          )}
          style={{ width: `${isDone ? 100 : progress}%` }}
        />
      </div>
      <div className="text-center text-xs text-muted-foreground">
        {isError ? '管线执行失败' : isDone ? '事件推理完成' : stage === 'idle' ? '就绪，等待触发' : `进度 ${progress}%`}
      </div>
    </div>
  )
}