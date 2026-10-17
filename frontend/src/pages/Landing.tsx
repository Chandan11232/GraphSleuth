import { Link } from 'react-router-dom'
import {
  ArrowRight, Brain, Bug, Crosshair, FlaskConical, GitCommitHorizontal, Moon, Network, ScanSearch, ShieldAlert, Sun,
  Terminal,
} from 'lucide-react'
import { InvestigationReplay } from '../components/landing/InvestigationReplay'
import { useTheme } from '../hooks'

/* Page structure: ink hero stage with a live replay → paper sections → ink closing band.
   Every number and name on this page comes from a real run on the bundled demo repo (acme-shop). */

const NAV = [
  { href: '#how', label: 'How it works' },
  { href: '#ranking', label: 'Ranking' },
  { href: '#before', label: 'Before the bug' },
  { href: '#model', label: 'Graph model' },
  { href: '#mcp', label: 'MCP' },
]

const STEPS = [
  {
    icon: ScanSearch,
    title: 'Ingest',
    body: 'tree-sitter parses Python, JavaScript, TypeScript, Go and Java into functions, classes, tests and call sites. Git history becomes Commit and Person nodes, linked to the exact functions each diff touched.',
    detail: 'acme-shop: 23 functions, 24 calls, 4 tests, 9 commits, 4 people, in under a second.',
  },
  {
    icon: Network,
    title: 'Investigate',
    body: 'From the bug report the agent walks the graph: stack frames, callers and callees up to three hops, covering tests, recent commits, PageRank. A local LLM can call the same tools for anything the sweep missed.',
    detail: '20 steps for the demo bug, each one a Cypher query you can expand.',
  },
  {
    icon: Crosshair,
    title: 'Explain',
    body: 'Suspects are ranked on six factors you can read. The evidence graph shows exactly the nodes and edges the agent visited, and the verdict comes with a patch, reviewers and blast radius.',
    detail: 'Reviewers are the people whose commits touched the function.',
  },
  {
    icon: Brain,
    title: 'Remember',
    body: 'Confirm the real root cause and a CONFIRMED edge is written into the graph. The next similar bug recalls it, whether it starts in the web app or from Claude Code over MCP.',
    detail: 'Memory is graph data, so any agent connected to it shares it.',
  },
]

const FACTORS = [
  { key: 'relevance', label: 'Matches report', weight: 0.4 },
  { key: 'recent_change', label: 'Recently changed', weight: 0.2 },
  { key: 'memory', label: 'Agent memory', weight: 0.15 },
  { key: 'untested', label: 'Untested', weight: 0.12 },
  { key: 'complexity', label: 'Complexity', weight: 0.08 },
  { key: 'centrality', label: 'Centrality', weight: 0.05 },
] as const

const SUSPECTS: { name: string; score: number; f: Record<(typeof FACTORS)[number]['key'], number> }[] = [
  { name: 'process_refund', score: 0.616, f: { relevance: 0.95, recent_change: 1, memory: 0, untested: 0, complexity: 0.13, centrality: 0.51 } },
  { name: 'handle_refund_request', score: 0.55, f: { relevance: 0.9, recent_change: 0.23, memory: 0, untested: 1, complexity: 0.13, centrality: 0.27 } },
  { name: 'validate_refund_amount', score: 0.51, f: { relevance: 1, recent_change: 0.11, memory: 0, untested: 0.5, complexity: 0.13, centrality: 0.35 } },
]

const RISKY = [
  { fn: 'update_stock', file: 'shop/db.py', who: 'Priya Raman', callers: 2 },
  { fn: 'fetch_order_row', file: 'shop/db.py', who: 'Priya Raman', callers: 2 },
  { fn: 'send_email', file: 'shop/notifications.py', who: 'Lee Okafor', callers: 1 },
  { fn: 'notify_refund', file: 'shop/notifications.py', who: 'Lee Okafor', callers: 1 },
  { fn: 'handle_order_status', file: 'shop/api/handlers.py', who: 'Marco Silva', callers: 0 },
]

const RELATIONSHIPS: [string, string, string][] = [
  ['Person', 'AUTHORED', 'Commit'],
  ['Commit', 'TOUCHED', 'Function'],
  ['Commit', 'MODIFIES', 'File'],
  ['Function', 'CALLS', 'Function'],
  ['Test', 'TESTS', 'Function'],
  ['File', 'CONTAINS', 'Function'],
  ['File', 'IMPORTS', 'File'],
  ['Class', 'HAS_METHOD', 'Function'],
  ['BugReport', 'MENTIONS', 'Function'],
  ['BugReport', 'INVESTIGATED_IN', 'Investigation'],
  ['Investigation', 'SUSPECTS', 'Function'],
  ['Investigation', 'CONFIRMED', 'Function'],
]

const LABEL_COLOR: Record<string, string> = {
  Person: 'text-fuchsia-600 dark:text-fuchsia-400',
  Commit: 'text-amber-600 dark:text-amber-400',
  Function: 'text-sky-600 dark:text-sky-400',
  File: 'text-slate-600 dark:text-slate-300',
  Class: 'text-slate-600 dark:text-slate-300',
  Test: 'text-emerald-600 dark:text-emerald-400',
  BugReport: 'text-red-600 dark:text-red-400',
  Investigation: 'text-violet-600 dark:text-violet-400',
}

const MCP_TOOLS = [
  'list_repositories', 'search_code', 'get_function', 'get_callers', 'get_callees', 'get_tests', 'recent_changes',
  'find_experts', 'blast_radius', 'call_path', 'hotspots', 'risky_changes', 'test_context',
  'recall_past_investigations', 'investigate_bug', 'confirm_root_cause', 'run_cypher',
]

function SectionHead({ eyebrow, title, children }: { eyebrow: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="max-w-2xl space-y-3">
      <p className="font-mono text-xs font-medium uppercase tracking-[0.14em] text-primary">{eyebrow}</p>
      <h2 className="font-display text-3xl font-semibold leading-[1.1] tracking-tight sm:text-4xl">{title}</h2>
      {children && <p className="text-base leading-relaxed text-muted-foreground">{children}</p>}
    </div>
  )
}

function Pattern({ from, rel, to }: { from: string; rel: string; to: string }) {
  return (
    <p className="font-mono text-[13px] whitespace-nowrap">
      <span className="text-muted-foreground">(</span><span className={LABEL_COLOR[from]}>{from}</span>
      <span className="text-muted-foreground">)-[:</span><span className="font-semibold">{rel}</span>
      <span className="text-muted-foreground">]-&gt;(</span><span className={LABEL_COLOR[to]}>{to}</span>
      <span className="text-muted-foreground">)</span>
    </p>
  )
}

export function Landing() {
  const { theme, toggle } = useTheme()

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <header className="sticky top-0 z-40 border-b border-white/10 bg-sidebar/90 text-sidebar-foreground backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-5 sm:px-8">
          <Link to="/" className="flex items-center gap-2.5">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary">
              <Bug className="h-4 w-4 text-primary-foreground" />
            </span>
            <span className="hidden whitespace-nowrap text-[15px] font-semibold min-[380px]:inline">Codebase Detective</span>
          </Link>
          <nav className="hidden flex-1 items-center gap-5 text-sm text-sidebar-muted md:flex" aria-label="Sections">
            {NAV.map(n => (
              <a key={n.href} href={n.href} className="transition-colors hover:text-sidebar-foreground">{n.label}</a>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={toggle}
              aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
              className="rounded-md p-2 text-sidebar-muted transition-colors hover:bg-white/10 hover:text-sidebar-foreground"
            >
              {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <Link
              to="/repos"
              className="inline-flex h-9 items-center gap-1.5 rounded-md bg-primary px-3.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
            >
              Open the app <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="relative overflow-hidden bg-sidebar text-sidebar-foreground">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle,rgba(148,163,184,0.13)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:linear-gradient(to_bottom,black_55%,transparent)]"
        />
        <div className="relative mx-auto grid grid-cols-1 max-w-6xl items-center gap-12 px-5 pb-20 pt-14 sm:px-8 lg:grid-cols-[0.9fr_1.1fr] lg:pt-20">
          <div className="space-y-7">
            <p className="font-mono text-xs font-medium uppercase tracking-[0.16em] text-teal-300">
              Bug investigation on a FalkorDB code graph
            </p>
            <h1 className="font-display text-[2.75rem] font-semibold leading-[1.02] tracking-tight sm:text-6xl">
              Find the function <span className="text-teal-300">that broke it.</span>
            </h1>
            <p className="max-w-xl text-lg leading-relaxed text-sidebar-muted">
              Codebase Detective turns a repository into a graph of functions, calls, tests, commits and the people
              behind them, then walks it hop by hop from a bug report to the root cause. Every step is a Cypher query
              you can read.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                to="/repos"
                className="inline-flex h-11 items-center gap-2 rounded-md bg-primary px-5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Investigate a repository <ArrowRight className="h-4 w-4" />
              </Link>
              <a
                href="#how"
                className="inline-flex h-11 items-center rounded-md border border-white/15 px-5 text-sm font-medium text-sidebar-foreground transition-colors hover:bg-white/5"
              >
                How it works
              </a>
            </div>
            <dl className="grid max-w-md grid-cols-3 gap-4 [&>div]:min-w-0 border-t border-white/10 pt-6">
              {[['5', 'languages parsed'], ['12', 'graph tools for the agent'], ['17', 'tools over MCP']].map(([n, l]) => (
                <div key={l}>
                  <dt className="sr-only">{l}</dt>
                  <dd className="font-display text-3xl font-semibold tabular-nums">{n}</dd>
                  <dd className="text-xs leading-snug text-sidebar-muted">{l}</dd>
                </div>
              ))}
            </dl>
          </div>
          <InvestigationReplay />
        </div>
      </section>

      {/* Why a graph */}
      <section className="mx-auto grid grid-cols-1 max-w-6xl items-center gap-10 px-5 py-20 sm:px-8 lg:grid-cols-2 lg:py-28">
        <SectionHead eyebrow="Why a graph" title="The questions behind a bug are about relationships.">
          What calls the function that crashed? Which of those changed last week, and does any test reach them? With
          text search and git blame that is an afternoon. In a graph it is one pattern.
        </SectionHead>
        <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
          <div className="flex items-center justify-between border-b px-4 py-2">
            <p className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">Cypher · acme-shop</p>
            <p className="font-mono text-[11px] text-muted-foreground">10 rows</p>
          </div>
          <pre className="overflow-x-auto bg-sidebar px-4 py-4 font-mono text-[12.5px] leading-relaxed text-sidebar-foreground">
{`MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f:Function)
      <-[:CALLS*1..3]-(:Function {name: 'handle_refund_request'})
WHERE NOT (:Test)-[:TESTS]->(f)
RETURN DISTINCT f.qualified_name, c.message, p.name`}
          </pre>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="text-xs text-muted-foreground">
                <tr className="border-b">
                  <th className="px-4 py-2 font-medium">function</th>
                  <th className="px-4 py-2 font-medium">commit</th>
                  <th className="px-4 py-2 font-medium">author</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {[
                  ['send_email', 'Customer refund notifications', 'Lee Okafor'],
                  ['notify_refund', 'Customer refund notifications', 'Lee Okafor'],
                  ['restock_items', 'Add inventory reservations', 'Marco Silva'],
                  ['validate_refund_amount', 'Refund processing with order validation', 'Priya Raman'],
                ].map(([f, c, p]) => (
                  <tr key={f}>
                    <td className="px-4 py-2 font-mono text-xs">{f}</td>
                    <td className="px-4 py-2 text-muted-foreground">{c}</td>
                    <td className="whitespace-nowrap px-4 py-2">{p}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="border-t px-4 py-2 text-xs text-muted-foreground">and 6 more rows</p>
          </div>
        </div>
      </section>

      {/* How it works */}
      <section id="how" className="scroll-mt-20 border-y bg-card">
        <div className="mx-auto max-w-6xl space-y-12 px-5 py-20 sm:px-8 lg:py-28">
          <SectionHead eyebrow="How it works" title="From repository to root cause in four steps." />
          <ol className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border bg-border sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s, i) => (
              <li key={s.title} className="flex flex-col gap-4 bg-card p-6">
                <div className="flex items-center justify-between">
                  <s.icon className="h-5 w-5 text-primary" />
                  <span className="font-mono text-xs text-muted-foreground tabular-nums">0{i + 1}</span>
                </div>
                <h3 className="font-display text-xl font-semibold">{s.title}</h3>
                <p className="flex-1 text-sm leading-relaxed text-muted-foreground">{s.body}</p>
                <p className="border-t pt-3 font-mono text-[11px] leading-relaxed">{s.detail}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Ranking */}
      <section id="ranking" className="scroll-mt-20 mx-auto grid grid-cols-1 max-w-6xl gap-12 px-5 py-20 sm:px-8 lg:grid-cols-[0.8fr_1.2fr] lg:py-28">
        <div className="space-y-8">
          <SectionHead eyebrow="Explainable ranking" title="A score you can argue with.">
            Every suspect gets six factors, all read from the graph. The weights are fixed and shown, so a reviewer can
            see why the agent chose a function, and disagree.
          </SectionHead>
          <ul className="space-y-2">
            {FACTORS.map(f => (
              <li key={f.key} className="flex items-center gap-3 text-sm">
                <span className="w-10 font-mono text-xs tabular-nums text-muted-foreground">{f.weight.toFixed(2)}</span>
                <span className="h-1.5 rounded-full bg-primary" style={{ width: `${f.weight * 220}px` }} />
                <span>{f.label}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Bug report: <span className="text-foreground">“Refund of unknown order returns 500”</span>, with a three-frame traceback.
          </p>
          {SUSPECTS.map((s, i) => (
            <div key={s.name} className={`rounded-xl border bg-card p-5 ${i === 0 ? 'border-red-300 dark:border-red-500/50' : ''}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="flex items-baseline gap-3">
                  <span className="font-mono text-xs text-muted-foreground">#{i + 1}</span>
                  <code className="font-mono text-sm font-semibold">{s.name}</code>
                  {i === 0 && (
                    <span className="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-semibold text-red-800 dark:bg-red-500/15 dark:text-red-300">
                      root cause · 79%
                    </span>
                  )}
                </div>
                <span className="font-display text-2xl font-semibold tabular-nums">{s.score.toFixed(2)}</span>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
                {FACTORS.map(f => (
                  <div key={f.key} className="flex items-center gap-2 text-[11px]">
                    <span className="w-24 shrink-0 text-muted-foreground">{f.label}</span>
                    <span className="h-1.5 flex-1 rounded-full bg-muted">
                      <span className="block h-1.5 rounded-full bg-primary" style={{ width: `${s.f[f.key] * 100}%` }} />
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">
            process_refund wins on a recent change: Dana Kim’s “Refactor refund flow: notify customer, simplify order
            lookup” switched it to <code className="font-mono">find_order</code>, which returns None for unknown orders.
          </p>
        </div>
      </section>

      {/* Before the bug */}
      <section id="before" className="scroll-mt-20 border-y bg-card">
        <div className="mx-auto grid grid-cols-1 max-w-6xl items-start gap-12 px-5 py-20 sm:px-8 lg:grid-cols-2 lg:py-28">
          <div className="space-y-8">
            <SectionHead eyebrow="Before the bug" title="See the risk before anyone files a ticket.">
              The dashboard lists functions changed in the last few commits that no test reaches, most-called first.
              One click opens an investigation of the change. Another drafts a test from the function’s callers,
              callees and the nearest existing tests in the graph.
            </SectionHead>
            <div className="rounded-xl border bg-background p-5 space-y-4">
              <div className="flex items-center gap-2 text-sm font-semibold">
                <FlaskConical className="h-4 w-4 text-primary" /> Test context for <code className="font-mono">update_stock</code>
              </div>
              <dl className="grid gap-3 text-sm sm:grid-cols-2">
                <div>
                  <dt className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">Called by</dt>
                  <dd className="font-mono text-xs mt-1">reserve_items, restock_items</dd>
                </div>
                <div>
                  <dt className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">Nearest test</dt>
                  <dd className="font-mono text-xs mt-1">test_full_refund_marks_order_refunded</dd>
                </div>
                <div>
                  <dt className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">Module state</dt>
                  <dd className="font-mono text-xs mt-1">_PRODUCTS, _ORDERS</dd>
                </div>
                <div>
                  <dt className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">Writes</dt>
                  <dd className="font-mono text-xs mt-1">tests/test_db.py · pytest</dd>
                </div>
              </dl>
              <p className="text-xs text-muted-foreground">
                A local model writes the draft. Run it before you commit it.
              </p>
            </div>
          </div>

          <div className="rounded-xl border bg-background shadow-sm">
            <div className="space-y-1 border-b px-5 py-4">
              <p className="flex items-center gap-2 text-sm font-semibold">
                <ShieldAlert className="h-4 w-4 text-risk-medium" /> Risky recent changes
              </p>
              <p className="text-xs text-muted-foreground">Untested functions changed in the last 10 commits, most-called first.</p>
            </div>
            <ul className="divide-y">
              {RISKY.map(r => (
                <li key={r.fn} className="flex gap-3 px-5 py-3">
                  <span
                    aria-hidden="true"
                    className={`w-1 shrink-0 rounded-full ${r.callers >= 2 ? 'bg-risk-high' : r.callers === 1 ? 'bg-risk-medium' : 'bg-risk-low'}`}
                  />
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-baseline gap-2">
                      <code className="font-mono text-sm font-medium">{r.fn}</code>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {r.callers} {r.callers === 1 ? 'caller' : 'callers'}
                      </span>
                    </p>
                    <p className="truncate text-xs text-muted-foreground">{r.file} · {r.who}</p>
                  </div>
                  <div className="hidden shrink-0 items-center gap-1.5 text-xs text-muted-foreground sm:flex" aria-hidden="true">
                    <span className="flex items-center gap-1 rounded-md border px-2 py-1"><Crosshair className="h-3 w-3" /> Investigate</span>
                    <span className="flex items-center gap-1 rounded-md border px-2 py-1"><FlaskConical className="h-3 w-3" /> Suggest test</span>
                  </div>
                </li>
              ))}
            </ul>
            <p className="border-t px-5 py-3 text-xs text-muted-foreground">
              0 callers is not safe: handlers like <code className="font-mono">handle_order_status</code> are entry points
              that web requests call directly.
            </p>
          </div>
        </div>
      </section>

      {/* Graph model */}
      <section id="model" className="scroll-mt-20 mx-auto max-w-6xl space-y-10 px-5 py-20 sm:px-8 lg:py-28">
        <SectionHead eyebrow="Graph model" title="Code, history and memory in one graph.">
          Each repository gets its own FalkorDB graph. Investigations are stored in it too, linked to the functions they
          suspected and confirmed, which is what lets the next investigation remember.
        </SectionHead>
        <div className="grid grid-cols-1 gap-x-10 gap-y-3 overflow-x-auto rounded-xl border bg-card p-6 md:grid-cols-2">
          {RELATIONSHIPS.map(([a, r, b]) => <Pattern key={`${a}${r}${b}`} from={a} rel={r} to={b} />)}
        </div>
        <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm text-muted-foreground">
          <span className="flex items-center gap-2"><GitCommitHorizontal className="h-4 w-4" /> Last 300 commits, mapped to function line spans</span>
          <span className="flex items-center gap-2"><ScanSearch className="h-4 w-4" /> Full-text index on function names and bodies</span>
          <span className="flex items-center gap-2"><Network className="h-4 w-4" /> PageRank over CALLS for hotspots</span>
        </div>
      </section>

      {/* MCP */}
      <section id="mcp" className="scroll-mt-20 border-t bg-card">
        <div className="mx-auto grid grid-cols-1 max-w-6xl items-center gap-12 px-5 py-20 sm:px-8 lg:grid-cols-2 lg:py-28">
          <div className="space-y-6">
            <SectionHead eyebrow="Model Context Protocol" title="The same graph, from Claude Code or Cursor.">
              The MCP server exposes the agent’s tools to any MCP client. Investigations started there appear in the web
              app’s memory, and the other way round.
            </SectionHead>
            <div className="flex flex-wrap gap-1.5">
              {MCP_TOOLS.map(t => (
                <span key={t} className="rounded-md border bg-background px-2 py-1 font-mono text-[11px]">{t}</span>
              ))}
            </div>
          </div>
          <div className="overflow-hidden rounded-xl border border-white/10 bg-sidebar text-sidebar-foreground shadow-xl">
            <div className="flex items-center gap-2 border-b border-white/10 px-4 py-2 font-mono text-[11px] text-sidebar-muted">
              <Terminal className="h-3.5 w-3.5" /> claude · codebase-detective
            </div>
            <div className="space-y-4 px-5 py-5 font-mono text-[12.5px] leading-relaxed">
              <p><span className="text-teal-300">›</span> Use codebase-detective to find who should review a change to process_refund</p>
              <p className="text-sidebar-muted">
                ⏺ codebase-detective · <span className="text-sidebar-foreground">find_experts</span>(repository: "acme-shop", function: "process_refund")
              </p>
              <pre className="overflow-x-auto rounded-md bg-black/30 px-3 py-2 text-[11.5px] text-slate-300">
{`MATCH (p1:Person)-[:AUTHORED]->(:Commit)-[:TOUCHED]->(f)
→ Dana Kim, Priya Raman`}
              </pre>
              <p>
                Dana Kim made the most recent change to <span className="text-teal-300">process_refund</span> (the refund-flow
                refactor) and Priya Raman wrote it. Ask Dana first.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* Closing */}
      <section className="relative overflow-hidden bg-sidebar text-sidebar-foreground">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle,rgba(148,163,184,0.12)_1px,transparent_1px)] [background-size:22px_22px] [mask-image:linear-gradient(to_top,black,transparent)]"
        />
        <div className="relative mx-auto flex max-w-6xl flex-col items-start gap-8 px-5 py-20 sm:px-8 lg:flex-row lg:items-end lg:justify-between">
          <div className="max-w-xl space-y-4">
            <h2 className="font-display text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl">
              Point it at a repository.
            </h2>
            <p className="text-sidebar-muted">
              Connect a public Git URL or the bundled demo. Everything runs locally: FalkorDB, tree-sitter and an
              optional Ollama model.
            </p>
          </div>
          <Link
            to="/repos"
            className="inline-flex h-12 items-center gap-2 rounded-md bg-primary px-6 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90"
          >
            Open the app <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
        <footer className="relative border-t border-white/10">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-5 py-6 text-xs text-sidebar-muted sm:px-8">
            <p>Built for Graph Hacks 2026 · WeMakeDevs × FalkorDB</p>
            <p>Tracks: Agents That Act on Connected Data · Agent Memory and Coordination</p>
            <p>MIT licensed</p>
          </div>
        </footer>
      </section>
    </div>
  )
}
