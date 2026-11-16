import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Moon } from 'lucide-react'
import { Mark, Wordmark } from './Mark'
import { api, type Health } from '../services/api'
import { ErrorBoundary } from './ErrorBoundary'
import { useTheme } from '../hooks'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `group flex items-baseline gap-3 border-l-2 py-2 pl-4 pr-3 text-sm transition-colors ${
    isActive ? 'border-signal bg-sidebar-active font-medium text-sidebar-foreground' : 'border-transparent text-sidebar-muted hover:text-sidebar-foreground'
  }`

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-1.5 w-1.5 shrink-0 ${ok ? 'bg-signal' : 'bg-risk-high'}`}
      aria-label={ok ? 'connected' : 'not connected'}
    />
  )
}

export function Layout() {
  const [health, setHealth] = useState<Health | null>(null)
  const location = useLocation()
  const { theme, toggle } = useTheme()
  useEffect(() => {
    const load = () => api.get('/health').then(r => setHealth(r.data)).catch(() => setHealth(null))
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [])

  return (
    <div className="flex h-screen bg-background">
      <aside className="flex w-56 shrink-0 flex-col bg-sidebar text-sidebar-foreground">
        <Link to="/" className="flex h-14 items-center gap-2 px-4" title="About GraphSleuth">
          <Mark className="h-6 w-6" />
          <Wordmark />
        </Link>
        <nav className="flex-1 space-y-px pt-4">
          <p className="px-4 pb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-sidebar-muted/70">Workspace</p>
          {[
            ['/repos', 'Repositories'],
            ['/investigate', 'New investigation'],
            ['/brain', 'Company brain'],
          ].map(([to, label], i) => (
            <NavLink key={to} to={to} className={navClass}>
              <span className="font-mono text-[10px] tabular-nums text-sidebar-muted">0{i + 1}</span>
              {label}
            </NavLink>
          ))}
        </nav>
        <button
          type="button"
          onClick={toggle}
          role="switch"
          aria-checked={theme === 'dark'}
          className="mx-4 mb-4 flex items-center gap-3 py-2 text-sm text-sidebar-muted transition-colors hover:text-sidebar-foreground"
        >
          <Moon className="h-4 w-4" />
          <span className="flex-1 text-left">Dark theme</span>
          <span className={`relative h-4 w-7 transition-colors ${theme === 'dark' ? 'bg-signal' : 'bg-white/15'}`} aria-hidden="true">
            <span
              className={`absolute top-0.5 h-3 w-3 transition-transform ${theme === 'dark' ? 'translate-x-3.5 bg-ink' : 'translate-x-0.5 bg-white'}`}
            />
          </span>
        </button>
        <div className="space-y-2 border-t border-white/10 p-4 font-mono text-[11px] text-sidebar-muted">
          <div className="flex items-center gap-2"><span className="flex-1">FalkorDB</span> <Dot ok={!!health?.falkordb} /></div>
          <div className="flex items-center gap-2">
            <span className="flex-1 truncate" title={health?.llm.host}>
              LLM {health?.llm.agent_model ?? (health?.llm.enabled ? 'no tool model' : 'off (graph-only)')}
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
