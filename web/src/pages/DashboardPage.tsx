import { useState, useEffect } from 'react'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { cleanEmptyRuns, deleteRun } from '@/api'
import { RunList } from '@/components/dashboard/RunList'
import { BacktestDetail } from '@/components/dashboard/BacktestDetail'
import { RunTabs } from '@/components/dashboard/RunTabs'
import { useRuns } from '@/hooks/useRuns'

type Notice = { type: 'success' | 'error'; message: string }

export function DashboardPage() {
  const { runs, loading, reload } = useRuns()

  // Tab state: multiple tabs can be open simultaneously
  const [openTabs, setOpenTabs] = useState<string[]>([])
  const [activeTab, setActiveTab] = useState<string>('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)

  useEffect(() => {
    if (!notice) return
    const timer = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(timer)
  }, [notice])

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
        setNotice({ type: 'error', message: detail || '删除失败' })
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
      setNotice({ type: 'error', message: '网络错误，删除失败' })
    }
  }

  const handleCleanRuns = async () => {
    try {
      const { error, data } = await cleanEmptyRuns()
      if (error) {
        const detail = (error as { detail?: string })?.detail
        setNotice({ type: 'error', message: detail || '清理失败' })
        return
      }
      // 清理后刷新列表，并给出结果反馈
      reload()
      if (data && data.deleted_runs > 0) {
        setNotice({
          type: 'success',
          message: `已清理 ${data.deleted_runs} 个无效回测记录（${data.deleted_records} 条日志）`,
        })
      } else {
        setNotice({ type: 'success', message: '没有需要清理的无效回测记录' })
      }
    } catch {
      setNotice({ type: 'error', message: '网络错误，清理失败' })
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      {notice && (
        <div
          role="status"
          className={`fixed top-3 right-3 z-[60] max-w-sm rounded-md border px-4 py-2 text-sm shadow-lg ${
            notice.type === 'error'
              ? 'border-destructive/30 bg-destructive/10 text-destructive'
              : 'border-success/30 bg-success/10 text-success'
          }`}
        >
          {notice.message}
        </div>
      )}
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
        <RunTabs
          openTabs={openTabs}
          activeTab={activeTab}
          runs={runs}
          onSelect={setActiveTab}
          onClose={closeTab}
        />
        {openTabs.length > 0 && (
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
        )}
      </div>
    </div>
  )
}
