import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Bug, Database, LayoutDashboard, Sparkles } from 'lucide-react'
import { api, type Health } from '../services/api'
import { ErrorBoundary } from './ErrorBoundary'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
    isActive ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
  }`

function Dot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${ok ? 'bg-green-500' : 'bg-red-500'}`} />
}

export function Layout() {
  const [health, setHealth] = useState<Health | null>(null)
  const location = useLocation()
  useEffect(() => {
    const load = () => api.get('/health').then(r => setHealth(r.data)).catch(() => setHealth(null))
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="flex h-screen bg-background">
      <aside className="w-60 border-r border-border bg-card flex flex-col shrink-0">
        <div className="p-4 border-b border-border">
          <div className="flex items-center gap-2">
            <div className="h-8 w-8 rounded-lg bg-primary/10 flex items-center justify-center">
              <Bug className="h-4 w-4 text-primary" />
            </div>
            <div>
              <h1 className="text-base font-bold leading-tight">Codebase Detective</h1>
              <p className="text-[11px] text-muted-foreground">bug hunting on a code graph</p>
            </div>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          <NavLink to="/" end className={navClass}>
            <LayoutDashboard className="h-4 w-4" /> Repositories
          </NavLink>
          <NavLink to="/investigate" className={navClass}>
            <Bug className="h-4 w-4" /> New investigation
          </NavLink>
        </nav>
        <div className="p-4 border-t border-border space-y-2 text-xs text-muted-foreground">
          <div className="flex items-center gap-2"><Database className="h-3.5 w-3.5" /> FalkorDB <Dot ok={!!health?.falkordb} /></div>
          <div className="flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5" />
            <span className="truncate" title={health?.llm.host}>
              LLM: {health?.llm.agent_model ?? (health?.llm.enabled ? 'no tool model' : 'off (graph-only)')}
            </span>
            <Dot ok={!!health?.llm.agent_model} />
          </div>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  )
}
