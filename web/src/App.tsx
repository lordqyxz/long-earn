import { BrowserRouter, Routes, Route, Navigate, NavLink, useLocation } from 'react-router-dom'
import { BarChart3, GitBranch, Layers, FlaskConical } from 'lucide-react'
import { cn } from '@/lib/utils'
import { DashboardPage } from '@/pages/DashboardPage'
import { EventFlowPage } from '@/pages/EventFlowPage'
import { ResearchPage } from '@/pages/ResearchPage'

const NAV_ITEMS = [
  { to: '/', label: '回测看板', icon: BarChart3 },
  { to: '/event-flow', label: '事件分析', icon: GitBranch },
  { to: '/research', label: '策略研发', icon: FlaskConical },
]

function Sidebar() {
  const location = useLocation()
  return (
    <aside className="w-52 border-r border-border bg-card/50 backdrop-blur flex flex-col h-screen sticky top-0">
      {/* Logo */}
      <div className="h-14 flex items-center gap-2 px-4 border-b border-border">
        <Layers className="h-5 w-5 text-primary" />
        <span className="font-semibold text-sm">Long Earn</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 p-2 space-y-1">
        {NAV_ITEMS.map((item) => {
          const isActive = location.pathname === item.to
          return (
            <NavLink
              key={item.to}
              to={item.to}
              className={cn(
                'flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors',
                isActive
                  ? 'bg-primary/10 text-primary font-medium'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground'
              )}
            >
              <item.icon className="h-4 w-4" />
              {item.label}
            </NavLink>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="px-4 py-3 border-t border-border text-xs text-muted-foreground">
        v2.0.0
      </div>
    </aside>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-hidden">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/event-flow" element={<EventFlowPage />} />
            <Route path="/research" element={<ResearchPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}