import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Bug, Database, LayoutDashboard, Library, Moon, Sparkles } from 'lucide-react'
import { api, type Health } from '../services/api'
import { ErrorBoundary } from './ErrorBoundary'
import { useTheme } from '../hooks'

const navClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${
    isActive ? 'bg-sidebar-active text-white font-medium' : 'text-sidebar-muted hover:bg-white/5 hover:text-sidebar-foreground'
  }`

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full shrink-0 ${ok ? 'bg-emerald-400' : 'bg-red-400'}`}
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
      <aside className="w-60 bg-sidebar text-sidebar-foreground flex flex-col shrink-0">
        <div className="px-4 py-5 border-b border-white/10">
          <Link to="/" className="flex items-center gap-2.5" title="About Codebase Detective">
            <div className="h-8 w-8 rounded-md bg-primary flex items-center justify-center">
              <Bug className="h-4 w-4 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-[15px] font-semibold leading-tight">Codebase Detective</h1>
              <p className="text-[11px] text-sidebar-muted">bug hunting on a code graph</p>
            </div>
          </Link>
        </div>
        <nav className="flex-1 p-3 space-y-1">
          <NavLink to="/repos" className={navClass}>
            <LayoutDashboard className="h-4 w-4" /> Repositories
          </NavLink>
          <NavLink to="/investigate" className={navClass}>
            <Bug className="h-4 w-4" /> New investigation
          </NavLink>
          <NavLink to="/brain" className={navClass}>
            <Library className="h-4 w-4" /> Company brain
          </NavLink>
        </nav>
        <div className="px-3 pb-3">
          <button
            type="button"
            onClick={toggle}
            role="switch"
            aria-checked={theme === 'dark'}
            className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm text-sidebar-muted hover:bg-white/5 hover:text-sidebar-foreground transition-colors"
          >
            <Moon className="h-4 w-4" />
            <span className="flex-1 text-left">Dark theme</span>
            <span
              className={`relative h-4 w-7 rounded-full transition-colors ${theme === 'dark' ? 'bg-primary' : 'bg-white/15'}`}
              aria-hidden="true"
            >
              <span
                className={`absolute top-0.5 h-3 w-3 rounded-full bg-white transition-transform ${theme === 'dark' ? 'translate-x-3.5' : 'translate-x-0.5'}`}
              />
            </span>
          </button>
        </div>
        <div className="p-4 border-t border-white/10 space-y-2 text-xs text-sidebar-muted">
          <p className="text-[10px] font-semibold uppercase tracking-[0.08em]">Services</p>
          <div className="flex items-center gap-2"><Database className="h-3.5 w-3.5" /> <span className="flex-1">FalkorDB</span> <Dot ok={!!health?.falkordb} /></div>
          <div className="flex items-center gap-2">
            <Sparkles className="h-3.5 w-3.5" />
            <span className="truncate flex-1" title={health?.llm.host}>
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
