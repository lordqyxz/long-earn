import { BarChart3, X } from 'lucide-react'
import type { RunInfo } from '@/api'
import { formatPercent } from '@/lib/utils'

interface Props {
  openTabs: string[]
  activeTab: string
  runs: RunInfo[]
  onSelect: (runId: string) => void
  onClose: (runId: string, e: React.MouseEvent) => void
}

export function RunTabs({ openTabs, activeTab, runs, onSelect, onClose }: Props) {
  if (openTabs.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center text-muted-foreground">
          <BarChart3 className="h-12 w-12 mx-auto mb-3 opacity-20" />
          <p className="text-sm">点击左侧列表中的回测运行以查看详情</p>
        </div>
      </div>
    )
  }

  return (
    <>
      {/* Tab bar */}
      <div className="flex items-stretch border-b border-border bg-muted/30 overflow-x-auto shrink-0">
        {openTabs.map((runId) => {
          const run = runs.find((r) => r.run_id === runId)
          const isActive = activeTab === runId
          const ret = run?.total_return ?? 0
          return (
            <div
              key={runId}
              role="tab"
              tabIndex={0}
              aria-selected={isActive}
              onClick={() => onSelect(runId)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  onSelect(runId)
                }
              }}
              className={`group flex items-center gap-2 px-3 py-2 border-r border-border cursor-pointer transition-colors whitespace-nowrap ${
                isActive
                  ? 'bg-background text-foreground'
                  : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
              }`}
            >
              <span className="text-xs truncate max-w-[120px]">
                {run?.strategy_id || runId.slice(0, 8)}
              </span>
              {run && (
                <span
                  className={`text-xs font-semibold ${
                    ret >= 0 ? 'text-success' : 'text-destructive'
                  }`}
                >
                  {formatPercent(ret, 1)}
                </span>
              )}
              <button
                className="ml-1 text-muted-foreground hover:text-destructive opacity-50 group-hover:opacity-100 transition-opacity"
                onClick={(e) => onClose(runId, e)}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
      </div>
    </>
  )
}
