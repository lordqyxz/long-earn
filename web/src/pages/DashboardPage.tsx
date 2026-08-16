import { useState } from 'react'
import { BarChart3, X, PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { cleanEmptyRuns, deleteRun } from '@/api'
import { RunList } from '@/components/dashboard/RunList'
import { BacktestDetail } from '@/components/dashboard/BacktestDetail'
import { useRuns } from '@/hooks/useRuns'
import { formatPercent } from '@/lib/utils'

export function DashboardPage() {
  const { runs, loading, reload } = useRuns()

  // Tab state: multiple tabs can be open simultaneously
  const [openTabs, setOpenTabs] = useState<string[]>([])
  const [activeTab, setActiveTab] = useState<string>('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)

  const openRun = (runId: string) => {
    if (!openTabs.includes(runId)) {
      setOpenTabs((prev) => [...prev, runId])
    }
    setActiveTab(runId)
  }

  const closeTab = (runId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    const idx = openTabs.indexOf(runId)
    const newTabs = openTabs.filter((id) => id !== runId)
    setOpenTabs(newTabs)
    if (activeTab === runId) {
      if (newTabs.length === 0) {
        setActiveTab('')
      } else {
        const newIdx = Math.min(idx, newTabs.length - 1)
        setActiveTab(newTabs[newIdx])
      }
    }
  }

  const handleDeleteRun = async (runId: string) => {
    try {
      const { error } = await deleteRun({ path: { run_id: runId } })
      if (error) {
        const detail = (error as { detail?: string })?.detail
        alert(detail || '删除失败')
        return
      }
      // 关闭已打开的 tab
      setOpenTabs((prev) => prev.filter((id) => id !== runId))
      if (activeTab === runId) {
        setActiveTab('')
      }
      // 刷新列表
      reload()
    } catch {
      alert('网络错误，删除失败')
    }
  }

  const handleCleanRuns = async () => {
    try {
      const { error, data } = await cleanEmptyRuns()
      if (error) {
        const detail = (error as { detail?: string })?.detail
        alert(detail || '清理失败')
        return
      }
      // 清理后刷新列表，并给出结果反馈
      reload()
      if (data && data.deleted_runs > 0) {
        alert(`已清理 ${data.deleted_runs} 个无效回测记录（${data.deleted_records} 条日志）`)
      } else {
        alert('没有需要清理的无效回测记录')
      }
    } catch {
      alert('网络错误，清理失败')
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left: Run list sidebar — collapsible */}
      <div
        className={`shrink-0 overflow-hidden border-r border-border transition-all duration-200 ${
          sidebarCollapsed ? 'w-0' : 'w-60'
        }`}
      >
        <RunList
          runs={runs}
          loading={loading}
          selectedRunId={activeTab || null}
          onSelect={openRun}
          onRefresh={reload}
          onDelete={handleDeleteRun}
          onClean={handleCleanRuns}
        />
      </div>

      {/* Collapse/expand toggle button */}
      <button
        onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
        className="z-10 flex items-center justify-center w-5 shrink-0 border-r border-border bg-muted/30 hover:bg-muted transition-colors"
        title={sidebarCollapsed ? '展开侧栏' : '收起侧栏'}
      >
        {sidebarCollapsed ? (
          <PanelLeftOpen className="h-4 w-4 text-muted-foreground" />
        ) : (
          <PanelLeftClose className="h-4 w-4 text-muted-foreground" />
        )}
      </button>

      {/* Right: Tabbed content area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {openTabs.length === 0 ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-muted-foreground">
              <BarChart3 className="h-12 w-12 mx-auto mb-3 opacity-20" />
              <p className="text-sm">点击左侧列表中的回测运行以查看详情</p>
            </div>
          </div>
        ) : (
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
                    onClick={() => setActiveTab(runId)}
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
                      onClick={(e) => closeTab(runId, e)}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )
              })}
            </div>

            {/* Tab content - all mounted, hidden if inactive */}
            <div className="flex-1 overflow-hidden">
              {openTabs.map((runId) => (
                <div
                  key={runId}
                  className={activeTab === runId ? 'h-full overflow-auto' : 'hidden'}
                >
                  <BacktestDetail runId={runId} />
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
