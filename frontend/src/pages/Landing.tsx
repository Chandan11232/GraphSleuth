import { Link } from 'react-router-dom'
import { ArrowRight, ArrowUpRight, Moon, Sun } from 'lucide-react'
import { InvestigationReplay } from '../components/landing/InvestigationReplay'
import { LayerStack } from '../components/landing/LayerStack'
import { Mark, Wordmark } from '../components/Mark'
import { useTheme } from '../hooks'

/* Page structure: an ink stage holding a 3D replay of a real investigation, then numbered paper sections laid out
   like a report, then an ink closing band. Every number and name comes from a real run on the demo repo (acme-shop). */

const NAV = [
  { href: '#how', label: 'How it works' },
  { href: '#ranking', label: 'Ranking' },
  { href: '#before', label: 'Before the bug' },
  { href: '#model', label: 'Graph model' },
  { href: '#mcp', label: 'MCP' },
]

const STEPS = [
  {
    title: 'Ingest',
    body: 'tree-sitter parses Python, JavaScript, TypeScript, Go and Java into functions, classes, tests and call sites. Git history becomes Commit and Person nodes, linked to the exact functions each diff touched. Issues, pull requests, reviews and CODEOWNERS join the same graph.',
    detail: 'acme-shop: 23 functions, 24 calls, 4 tests, 9 commits, 9 pull requests, 4 teams, in under a second.',
  },
  {
    title: 'Investigate',
    body: 'From the bug report the agent walks the graph: stack frames, callers and callees up to three hops, covering tests, recent commits, PageRank. A local LLM can call the same tools for anything the sweep missed.',
    detail: '22 steps for the demo bug, each one a Cypher query you can expand.',
  },
  {
    title: 'Explain',
    body: 'Suspects are ranked on six factors you can read. The verdict comes with a patch, blast radius, the pull requests and decisions that shaped the code, and who owns it.',
    detail: 'acme-shop: PR #12 reversed a review decision from PR #6, approved by nobody in @payments.',
  },
  {
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

const MCP_TOOLS = [
  'list_repositories', 'search_code', 'get_function', 'get_callers', 'get_callees', 'get_tests', 'recent_changes',
  'find_experts', 'blast_radius', 'call_path', 'hotspots', 'risky_changes', 'test_context',
  'recall_past_investigations', 'investigate_bug', 'confirm_root_cause', 'why_code', 'who_owns', 'search_knowledge',
  'decisions', 'ask_company_brain', 'run_cypher',
]

const FACTS = [
  ['5', 'languages parsed'],
  ['12', 'graph tools for the agent'],
  ['17', 'tools over MCP'],
  ['1 of 4', 'tests the graph picked to run'],
]

const ROWS = [
  ['send_email', 'Customer refund notifications', 'Lee Okafor'],
  ['notify_refund', 'Customer refund notifications', 'Lee Okafor'],
  ['restock_items', 'Add inventory reservations', 'Marco Silva'],
  ['validate_refund_amount', 'Refund processing with order validation', 'Priya Raman'],
]

/** A numbered section: the number and label sit in a narrow gutter, the content takes the rest. */
function Section({ n, label, id, children }: { n: string; label: string; id?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-14 border-t">
      <div className="mx-auto grid max-w-[1320px] grid-cols-1 gap-x-8 gap-y-8 px-5 py-16 sm:px-8 lg:grid-cols-[9.5rem_minmax(0,1fr)] lg:py-24">
        <p className="flex items-baseline gap-3 font-mono text-xs uppercase tracking-[0.12em] text-muted-foreground lg:sticky lg:top-20 lg:block lg:self-start lg:space-y-2">
          <span className="block font-display text-4xl font-bold tracking-[-0.03em] text-foreground lg:text-5xl">{n}</span>
          <span className="block">{label}</span>
        </p>
        <div className="min-w-0">{children}</div>
      </div>
    </section>
  )
}

function Title({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="max-w-3xl font-display text-[2rem] font-bold leading-[1.04] tracking-[-0.025em] sm:text-5xl">
      {children}
    </h2>
  )
}

const Lede = ({ children }: { children: React.ReactNode }) => (
  <p className="mt-5 max-w-2xl text-[17px] leading-relaxed text-muted-foreground">{children}</p>
)

const Caption = ({ children }: { children: React.ReactNode }) => (
  <p className="font-mono text-[11px] uppercase tracking-[0.1em] text-muted-foreground">{children}</p>
)

export function Landing() {
  const { theme, toggle } = useTheme()

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="stage sticky top-0 z-40 border-b stage-rule">
        <div className="mx-auto flex h-12 max-w-[1320px] items-center gap-8 px-5 sm:px-8">
          <Link to="/" className="flex items-center gap-2">
            <Mark className="h-6 w-6" />
            <Wordmark />
          </Link>
          <nav className="hidden flex-1 items-center gap-6 font-mono text-[11px] uppercase tracking-[0.1em] text-white/50 md:flex" aria-label="Sections">
            {NAV.map(n => (
              <a key={n.href} href={n.href} className="transition-colors hover:text-white">{n.label}</a>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={toggle}
              aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
              className="p-2 text-white/50 transition-colors hover:text-white"
            >
              {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <Link
              to="/repos"
              className="inline-flex h-8 items-center gap-1.5 bg-signal px-3 text-[13px] font-semibold text-ink transition-colors hover:bg-white"
            >
              Open the app <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
        </div>
      </header>

      {/* Hero */}
      <section className="stage">
        <div className="mx-auto grid max-w-[1320px] grid-cols-1 items-start gap-x-10 gap-y-12 px-5 pb-14 pt-12 sm:px-8 lg:grid-cols-12 lg:pb-20 lg:pt-20">
          <div className="lg:col-span-5 lg:pt-6">
            <p className="font-mono text-[11px] uppercase tracking-[0.14em] text-white/50">
              Bug investigation on a FalkorDB code graph
            </p>
            <h1 className="mt-6 font-display text-[clamp(2.9rem,5.6vw,4.75rem)] font-extrabold leading-[0.95] tracking-[-0.03em]">
              Find the function <span className="marked text-ink">that broke it.</span>
            </h1>
            <p className="mt-8 max-w-md text-[17px] leading-relaxed text-white/60">
              GraphSleuth turns a repository into a graph of functions, calls, tests, commits and the people behind
              them, then walks it hop by hop from a bug report to the root cause. Every step is a Cypher query you
              can read.
            </p>
            <div className="mt-9 flex flex-wrap items-center gap-x-6 gap-y-3">
              <Link
                to="/repos"
                className="inline-flex h-12 items-center gap-2 bg-signal px-5 text-[15px] font-semibold text-ink transition-colors hover:bg-white"
              >
                Investigate a repository <ArrowRight className="h-4 w-4" />
              </Link>
              <a href="#how" className="text-[15px] font-medium text-white/80 underline decoration-white/30 underline-offset-4 transition-colors hover:text-white hover:decoration-white">
                How it works
              </a>
            </div>
          </div>
          <div className="min-w-0 lg:col-span-7">
            <InvestigationReplay />
          </div>
        </div>
        <dl className="mx-auto grid max-w-[1320px] grid-cols-2 border-t stage-rule px-5 sm:px-8 lg:grid-cols-4">
          {FACTS.map(([n, l]) => (
            <div key={l} className="flex items-baseline gap-3 border-white/10 py-5 pr-4 lg:[&:not(:first-child)]:border-l lg:[&:not(:first-child)]:pl-6">
              <dd className="whitespace-nowrap font-display text-3xl font-bold tabular-nums tracking-[-0.03em]">{n}</dd>
              <dt className="text-[13px] leading-snug text-white/50">{l}</dt>
            </div>
          ))}
        </dl>
      </section>

      <Section n="01" label="Why a graph">
        <Title>The questions behind a bug are about relationships.</Title>
        <Lede>
          What calls the function that crashed? Which of those changed last week, and does any test reach them? With
          text search and git blame that is an afternoon. In a graph it is one pattern.
        </Lede>
        <figure className="mt-10">
          <pre className="stage overflow-x-auto px-5 py-5 font-mono text-[13px] leading-relaxed">
{`MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f:Function)
      <-[:CALLS*1..3]-(:Function {name: 'handle_refund_request'})
WHERE NOT (:Test)-[:TESTS]->(f)
RETURN DISTINCT f.qualified_name, c.message, p.name`}
          </pre>
          <div className="overflow-x-auto border-x border-b bg-card">
            <table className="w-full text-left text-[13px]">
              <thead>
                <tr className="border-b font-mono text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
                  <th className="px-5 py-2.5 font-medium">function</th>
                  <th className="px-5 py-2.5 font-medium">commit</th>
                  <th className="px-5 py-2.5 font-medium">author</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {ROWS.map(([f, c, p]) => (
                  <tr key={f}>
                    <td className="px-5 py-2.5 font-mono text-xs">{f}</td>
                    <td className="px-5 py-2.5 text-muted-foreground">{c}</td>
                    <td className="whitespace-nowrap px-5 py-2.5">{p}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <figcaption className="mt-3"><Caption>acme-shop · 10 rows, 4 shown</Caption></figcaption>
        </figure>
      </Section>

      <Section n="02" label="How it works" id="how">
        <Title>From repository to root cause in four steps.</Title>
        <ol className="mt-10 border-b">
          {STEPS.map((s, i) => (
            <li key={s.title} className="grid grid-cols-1 gap-x-8 gap-y-3 border-t py-7 md:grid-cols-[3rem_10rem_minmax(0,1fr)] xl:grid-cols-[3rem_11rem_minmax(0,1fr)_17rem]">
              <span className="font-mono text-xs tabular-nums text-muted-foreground md:pt-2">0{i + 1}</span>
              <h3 className="font-display text-[1.75rem] font-bold leading-none tracking-[-0.03em]">{s.title}</h3>
              <p className="leading-relaxed text-muted-foreground">{s.body}</p>
              <p className="font-mono text-xs leading-relaxed md:col-start-3 xl:col-start-4 xl:border-l xl:pl-6">{s.detail}</p>
            </li>
          ))}
        </ol>
      </Section>

      <Section n="03" label="Ranking" id="ranking">
        <Title>A score you can argue with.</Title>
        <Lede>
          Every suspect gets six factors, all read from the graph. The weights are fixed and shown, so a reviewer can
          see why the agent chose a function, and disagree.
        </Lede>

        <div className="mt-10">
          <Caption>Weights, summing to 1.00</Caption>
          <div className="mt-3 flex h-9 gap-px" aria-hidden="true">
            {FACTORS.map((f, i) => (
              <span
                key={f.key}
                className="flex items-center bg-primary pl-2 font-mono text-[11px] tabular-nums text-primary-foreground"
                style={{ width: `${f.weight * 100}%`, opacity: 1 - i * 0.13 }}
              >
                {f.weight >= 0.12 && f.weight.toFixed(2)}
              </span>
            ))}
          </div>
          <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[13px]">
            {FACTORS.map(f => (
              <li key={f.key}>
                {f.label} <span className="font-mono text-xs tabular-nums text-muted-foreground">{f.weight.toFixed(2)}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="mt-12">
          <p className="text-sm text-muted-foreground">
            Bug report: <span className="text-foreground">“Refund of unknown order returns 500”</span>, with a
            three-frame traceback.
          </p>
          <ol className="mt-4 border-b">
            {SUSPECTS.map((s, i) => (
              <li key={s.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-8 gap-y-4 border-t py-5 lg:grid-cols-[17rem_minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <code className="block truncate font-mono text-sm font-semibold">
                    {i === 0 ? <span className="marked text-ink">{s.name}</span> : s.name}
                  </code>
                  <p className="mt-1.5 font-mono text-[11px] uppercase tracking-[0.1em] text-muted-foreground">
                    {i === 0 ? 'Root cause · 79% confidence' : `Suspect ${i + 1}`}
                  </p>
                </div>
                <div className="col-span-2 row-start-2 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3 lg:col-span-1 lg:row-start-auto">
                  {FACTORS.map(f => (
                    <div key={f.key} className="text-[11px]">
                      <span className="text-muted-foreground">{f.label}</span>
                      <span className="mt-1 block h-[3px] bg-foreground/10">
                        <span className="block h-full bg-foreground" style={{ width: `${s.f[f.key] * 100}%` }} />
                      </span>
                    </div>
                  ))}
                </div>
                <span className="font-display text-4xl font-bold tabular-nums leading-none tracking-[-0.03em]">
                  {s.score.toFixed(2)}
                </span>
              </li>
            ))}
          </ol>
          <p className="mt-4 max-w-2xl text-sm text-muted-foreground">
            process_refund wins on a recent change: Dana Kim’s “Refactor refund flow: notify customer, simplify order
            lookup” switched it to <code className="font-mono text-foreground">find_order</code>, which returns None
            for unknown orders.
          </p>
        </div>
      </Section>

      <Section n="04" label="Before the bug" id="before">
        <Title>See the risk before anyone files a ticket.</Title>
        <Lede>
          The dashboard lists functions changed in the last few commits that no test reaches, most-called first. One
          click opens an investigation of the change. Another drafts a test from the function’s callers, callees and
          the nearest existing tests in the graph.
        </Lede>
        <div className="mt-10 grid grid-cols-1 gap-x-12 gap-y-10 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <div>
            <Caption>Risky recent changes · last 10 commits</Caption>
            <ul className="mt-3 border-b">
              {RISKY.map(r => (
                <li key={r.fn} className="flex items-center gap-4 border-t py-3">
                  <span
                    aria-hidden="true"
                    className={`h-9 w-1 shrink-0 ${r.callers >= 2 ? 'bg-risk-high' : r.callers === 1 ? 'bg-risk-medium' : 'bg-risk-low'}`}
                  />
                  <div className="min-w-0 flex-1">
                    <code className="font-mono text-sm font-medium">{r.fn}</code>
                    <p className="truncate text-xs text-muted-foreground">{r.file} · {r.who}</p>
                  </div>
                  <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                    {r.callers} {r.callers === 1 ? 'caller' : 'callers'}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-4 text-sm text-muted-foreground">
              0 callers is not safe: handlers like <code className="font-mono text-foreground">handle_order_status</code>{' '}
              are entry points that web requests call directly.
            </p>
          </div>
          <div>
            <Caption>Test context for update_stock</Caption>
            <dl className="mt-3 border-b">
              {[
                ['Called by', 'reserve_items, restock_items'],
                ['Nearest test', 'test_full_refund_marks_order_refunded'],
                ['Module state', '_PRODUCTS, _ORDERS'],
                ['Writes', 'tests/test_db.py · pytest'],
              ].map(([k, v]) => (
                <div key={k} className="grid grid-cols-[7rem_minmax(0,1fr)] gap-4 border-t py-3">
                  <dt className="text-sm text-muted-foreground">{k}</dt>
                  <dd className="break-words font-mono text-xs leading-5">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-4 text-sm text-muted-foreground">A local model writes the draft. Run it before you commit it.</p>
          </div>
        </div>
      </Section>

      <Section n="05" label="Graph model" id="model">
        <Title>Code, history, team knowledge and memory in one graph.</Title>
        <Lede>
          Each repository gets its own FalkorDB graph. The issues, pull requests, reviews and decisions around the code
          live in it too, and so do investigations, linked to the functions they suspected and confirmed.
        </Lede>
        <div className="mt-6">
          <LayerStack />
        </div>
        <ul className="mt-6 grid grid-cols-1 gap-x-10 border-b text-sm sm:grid-cols-2">
          {[
            'Last 300 commits, mapped to function line spans',
            'Full-text index on function names and bodies',
            'PageRank over CALLS for hotspots',
            'Decisions flagged when the code they govern changes',
          ].map(t => (
            <li key={t} className="border-t py-3">{t}</li>
          ))}
        </ul>
      </Section>

      <Section n="06" label="MCP" id="mcp">
        <Title>The same graph, from Claude Code or Cursor.</Title>
        <Lede>
          The MCP server exposes the agent’s tools to any MCP client. Investigations started there appear in the web
          app’s memory, and the other way round.
        </Lede>
        <div className="mt-10 grid grid-cols-1 gap-x-12 gap-y-10 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
          <div>
            <Caption>{MCP_TOOLS.length} tools</Caption>
            <p className="mt-3 font-mono text-[12.5px] leading-7">
              {MCP_TOOLS.map((t, i) => (
                <span key={t}>
                  {t}
                  {i < MCP_TOOLS.length - 1 && <span className="text-muted-foreground"> · </span>}
                </span>
              ))}
            </p>
          </div>
          <div className="stage space-y-4 px-5 py-5 font-mono text-[12.5px] leading-relaxed">
            <p className="text-[11px] text-white/40">claude · graphsleuth</p>
            <p><span className="text-signal">›</span> Use graphsleuth to find who should review a change to process_refund</p>
            <p className="text-white/50">
              ⏺ graphsleuth · <span className="text-white/90">find_experts</span>(repository: "acme-shop", function: "process_refund")
            </p>
            <pre className="overflow-x-auto border-l border-white/20 pl-3 text-[11.5px] text-white/60">
{`MATCH (p1:Person)-[:AUTHORED]->(:Commit)-[:TOUCHED]->(f)
→ Dana Kim, Priya Raman`}
            </pre>
            <p>
              Dana Kim made the most recent change to <span className="text-signal">process_refund</span> (the
              refund-flow refactor) and Priya Raman wrote it. Ask Dana first.
            </p>
          </div>
        </div>
      </Section>

      {/* Closing */}
      <section className="stage">
        <div className="mx-auto max-w-[1320px] px-5 py-20 sm:px-8 lg:py-28">
          <h2 className="font-display text-[clamp(2.75rem,8vw,7rem)] font-extrabold leading-[0.92] tracking-[-0.035em]">
            Point it at a <span className="marked text-ink">repository.</span>
          </h2>
          <div className="mt-10 flex flex-col items-start gap-8 sm:flex-row sm:items-center sm:justify-between">
            <p className="max-w-md text-white/60">
              Connect a public Git URL or the bundled demo. Everything runs locally: FalkorDB, tree-sitter and an
              optional Ollama model.
            </p>
            <Link
              to="/repos"
              className="inline-flex h-12 shrink-0 items-center gap-2 bg-signal px-5 text-[15px] font-semibold text-ink transition-colors hover:bg-white"
            >
              Open the app <ArrowUpRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
        <footer className="border-t stage-rule">
          <div className="mx-auto flex max-w-[1320px] flex-wrap items-center justify-between gap-x-8 gap-y-2 px-5 py-5 font-mono text-[11px] text-white/45 sm:px-8">
            <p>Built for Graph Hacks 2026 · WeMakeDevs × FalkorDB</p>
            <p>Agents That Act on Connected Data · Agent Memory and Coordination · Company Brain</p>
            <p>MIT licensed</p>
          </div>
        </footer>
      </section>
    </div>
  )
}
