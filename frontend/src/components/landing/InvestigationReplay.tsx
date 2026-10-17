import { useEffect, useState } from 'react'
import { Pause, Play, RotateCcw } from 'lucide-react'

/*
 * A replay of a real investigation on the bundled demo repo (acme-shop): the bug report
 * "Refund of unknown order returns 500". Step summaries, scores and Cypher are copied from
 * that run; the graph shows the nodes and edges each step touched.
 */

type NodeId = 'bug' | 'handle' | 'process' | 'validate' | 'test' | 'find' | 'notify' | 'commit' | 'dana'
type EdgeType = 'MENTIONS' | 'CALLS' | 'TESTS' | 'TOUCHED' | 'AUTHORED'

const EDGE_COLOR: Record<EdgeType, string> = {
  MENTIONS: '#f87171',
  CALLS: '#60a5fa',
  TESTS: '#4ade80',
  TOUCHED: '#fbbf24',
  AUTHORED: '#e879f9',
}

const W = 190
const H = 46

const NODES: Record<NodeId, { x: number; y: number; kind: string; label: string; w?: number }> = {
  bug: { x: 8, y: 150, kind: 'BugReport', label: 'Refund of unknown order', w: 196 },
  handle: { x: 238, y: 40, kind: 'Function', label: 'handle_refund_request' },
  process: { x: 238, y: 150, kind: 'Function', label: 'process_refund' },
  validate: { x: 238, y: 260, kind: 'Function', label: 'validate_refund_amount' },
  test: { x: 470, y: 8, kind: 'Test', label: 'test_full_refund_marks…' },
  find: { x: 470, y: 78, kind: 'Function', label: 'find_order' },
  notify: { x: 470, y: 148, kind: 'Function', label: 'notify_refund' },
  commit: { x: 470, y: 218, kind: 'Commit', label: 'Refactor refund flow…' },
  dana: { x: 470, y: 296, kind: 'Person', label: 'Dana Kim' },
}

const KIND_COLOR: Record<string, string> = {
  BugReport: '#f87171', Function: '#60a5fa', Test: '#4ade80', Commit: '#fbbf24', Person: '#e879f9',
}

type Edge = { id: string; from: NodeId; to: NodeId; type: EdgeType; d: string }

const EDGES: Edge[] = [
  { id: 'm1', from: 'bug', to: 'handle', type: 'MENTIONS', d: 'M204 173 C222 173 220 63 238 63' },
  { id: 'm2', from: 'bug', to: 'process', type: 'MENTIONS', d: 'M204 173 L238 173' },
  { id: 'm3', from: 'bug', to: 'validate', type: 'MENTIONS', d: 'M204 173 C222 173 220 283 238 283' },
  { id: 'c1', from: 'handle', to: 'process', type: 'CALLS', d: 'M333 86 L333 150' },
  { id: 'c2', from: 'process', to: 'validate', type: 'CALLS', d: 'M333 196 L333 260' },
  { id: 'c3', from: 'process', to: 'find', type: 'CALLS', d: 'M428 166 C450 166 448 101 470 101' },
  { id: 'c4', from: 'process', to: 'notify', type: 'CALLS', d: 'M428 173 L470 171' },
  { id: 't1', from: 'test', to: 'process', type: 'TESTS', d: 'M470 31 C440 31 448 156 428 158' },
  { id: 'r1', from: 'commit', to: 'process', type: 'TOUCHED', d: 'M470 241 C448 241 450 188 428 186' },
  { id: 'a1', from: 'dana', to: 'commit', type: 'AUTHORED', d: 'M565 296 L565 264' },
]

interface Phase {
  step: string
  tool: string
  summary: string
  queryLabel: 'Cypher' | 'Scoring'
  query: string
  nodes: NodeId[]
  edges: string[]
}

const PHASES: Phase[] = [
  {
    step: '2 / 20',
    tool: 'match_stack_frames',
    summary: '3/3 frames matched: handle_refund_request, process_refund, validate_refund_amount',
    queryLabel: 'Cypher',
    query: `MATCH (f:Function)
WHERE f.name = $name AND f.file_path ENDS WITH $file_suffix
RETURN f.id, f.qualified_name, f.file_path, f.start_line`,
    nodes: ['bug', 'handle', 'process', 'validate'],
    edges: ['m1', 'm2', 'm3'],
  },
  {
    step: '7 / 20',
    tool: 'get_callers · get_callees',
    summary: 'process_refund calls 6 repo functions: find_order, mark_refunded, notify_refund, refund_card…',
    queryLabel: 'Cypher',
    query: `MATCH p = (caller:Function)-[:CALLS*1..3]->(f:Function {id: $id})
WHERE length(p) <= $depth AND caller.id <> $id
WITH caller, min(length(p)) AS distance
RETURN caller.qualified_name AS name, distance`,
    nodes: ['find', 'notify'],
    edges: ['c1', 'c2', 'c3', 'c4'],
  },
  {
    step: '11 / 20',
    tool: 'get_tests',
    summary: '8/10 functions have tests; untested: handle_order_status, handle_refund_request',
    queryLabel: 'Cypher',
    query: `MATCH (f:Function {id: fid})
OPTIONAL MATCH (t:Test)-[:TESTS]->(f)
OPTIONAL MATCH (ti:Test)-[:TESTS]->(:Function)-[:CALLS*1..2]->(f)
RETURN f.qualified_name, collect(DISTINCT t), collect(DISTINCT ti)`,
    nodes: ['test'],
    edges: ['t1'],
  },
  {
    step: '12 / 20',
    tool: 'recent_changes',
    summary: 'most recent: process_refund (Dana Kim: "Refactor refund flow: notify customer, simplify order lookup")',
    queryLabel: 'Cypher',
    query: `MATCH (f:Function {id: fid})
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(c:Commit)-[:TOUCHED]->(f)
WITH f, c, p ORDER BY c.timestamp DESC
RETURN f.qualified_name, collect({message: c.message, author: p.name})`,
    nodes: ['commit', 'dana'],
    edges: ['r1', 'a1'],
  },
  {
    step: '15 / 20',
    tool: 'rank_suspects',
    summary: 'process_refund 0.62 · handle_refund_request 0.55 · validate_refund_amount 0.51',
    queryLabel: 'Scoring',
    query: `score = 0.40 relevance  + 0.20 recent_change + 0.15 memory
      + 0.12 untested   + 0.08 complexity    + 0.05 centrality

process_refund = 0.40·0.95 + 0.20·1.00 + 0.08·0.13 + 0.05·0.51 = 0.62`,
    nodes: [],
    edges: [],
  },
  {
    step: '16 / 20',
    tool: 'verdict · find_experts',
    summary: 'Root cause: process_refund (79% confidence). Reviewers: Dana Kim, Priya Raman',
    queryLabel: 'Cypher',
    query: `MATCH (f:Function {id: $id})
OPTIONAL MATCH (p1:Person)-[:AUTHORED]->(:Commit)-[:TOUCHED]->(f)
RETURN DISTINCT p1.name AS name, count(*) AS fn_commits
ORDER BY fn_commits DESC`,
    nodes: [],
    edges: [],
  },
]

const SCORES: Partial<Record<NodeId, string>> = { handle: '0.55', process: '0.62', validate: '0.51' }
const STEP_MS = 2600

function reducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function InvestigationReplay() {
  const [phase, setPhase] = useState(() => (reducedMotion() ? PHASES.length - 1 : 0))
  const [playing, setPlaying] = useState(() => !reducedMotion())

  useEffect(() => {
    if (!playing) return
    const last = phase === PHASES.length - 1
    const t = setTimeout(() => setPhase(last ? 0 : phase + 1), last ? STEP_MS * 2.2 : STEP_MS)
    return () => clearTimeout(t)
  }, [phase, playing])

  const seenNodes = new Set<NodeId>(PHASES.slice(0, phase + 1).flatMap(p => p.nodes))
  const seenEdges = new Set<string>(PHASES.slice(0, phase + 1).flatMap(p => p.edges))
  const current = PHASES[phase]
  const ranked = phase >= 4
  const solved = phase >= 5

  return (
    <div className="rounded-xl border border-white/10 bg-[hsl(224_34%_9%)] shadow-2xl shadow-black/40 overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-2.5">
        <p className="font-mono text-[11px] text-slate-400 truncate">
          acme-shop · <span className="text-slate-200">Refund of unknown order returns 500</span>
        </p>
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] font-medium ${
            solved ? 'bg-red-500/15 text-red-300' : 'bg-teal-400/10 text-teal-300'
          }`}
        >
          {solved ? 'root cause found' : `step ${current.step}`}
        </span>
      </div>

      <div className="px-2 pt-3 sm:px-4">
        <svg viewBox="0 0 668 350" className="w-full h-auto" role="img"
             aria-label="Graph of the investigation: the bug report mentions three functions; process_refund is the root cause">
          <defs>
            {(Object.keys(EDGE_COLOR) as EdgeType[]).map(t => (
              <marker key={t} id={`arrow-${t}`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M0 0 L8 4 L0 8 z" fill={EDGE_COLOR[t]} />
              </marker>
            ))}
          </defs>

          {EDGES.map(e => {
            const on = seenEdges.has(e.id)
            return (
              <path
                key={e.id}
                d={e.d}
                pathLength={1}
                fill="none"
                stroke={EDGE_COLOR[e.type]}
                strokeWidth={1.6}
                markerEnd={on ? `url(#arrow-${e.type})` : undefined}
                style={{
                  strokeDasharray: 1,
                  strokeDashoffset: on ? 0 : 1,
                  transition: 'stroke-dashoffset 900ms ease-out',
                }}
              />
            )
          })}

          {(Object.keys(NODES) as NodeId[]).map(id => {
            const n = NODES[id]
            const on = seenNodes.has(id)
            const root = id === 'process' && solved
            const w = n.w ?? W
            return (
              <g key={id} style={{ opacity: on ? 1 : 0.18, transition: 'opacity 600ms ease' }}>
                {root && (
                  <rect x={n.x - 5} y={n.y - 5} width={w + 10} height={H + 10} rx={10}
                        fill="none" stroke="#ef4444" strokeWidth={2} className="motion-safe:animate-pulse" />
                )}
                <rect x={n.x} y={n.y} width={w} height={H} rx={7}
                      fill="hsl(222 28% 14%)" stroke={root ? '#ef4444' : KIND_COLOR[n.kind]}
                      strokeOpacity={root ? 1 : 0.55} strokeWidth={root ? 1.8 : 1.2} />
                <text x={n.x + 10} y={n.y + 17} className="font-mono" fontSize={9} letterSpacing="0.08em"
                      fill={root ? '#fca5a5' : KIND_COLOR[n.kind]}>
                  {root ? 'ROOT CAUSE · 79%' : n.kind.toUpperCase()}
                </text>
                <text x={n.x + 10} y={n.y + 34} className="font-mono" fontSize={12} fill="#e2e8f0">
                  {n.label}
                </text>
                {ranked && SCORES[id] && (
                  <text x={n.x + w - 10} y={n.y + 17} textAnchor="end" className="font-mono" fontSize={10}
                        fontWeight={600} fill={id === 'process' ? '#fca5a5' : '#94a3b8'}>
                    {SCORES[id]}
                  </text>
                )}
              </g>
            )
          })}
        </svg>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pb-3 font-mono text-[10px] text-slate-400">
        {(Object.keys(EDGE_COLOR) as EdgeType[]).map(t => (
          <span key={t} className="flex items-center gap-1.5">
            <span className="h-0.5 w-3 rounded" style={{ background: EDGE_COLOR[t] }} /> {t}
          </span>
        ))}
      </div>

      <div className="border-t border-white/10 bg-black/20 px-4 py-3 space-y-2">
        <div className="flex items-start justify-between gap-3">
          <p className="text-[13px] leading-snug text-slate-200 min-h-[2.6em]">
            <span className="font-mono text-teal-300">{current.tool}</span>
            <span className="text-slate-500"> → </span>
            {current.summary}
          </p>
          <div className="flex shrink-0 gap-1">
            <button
              type="button"
              onClick={() => setPlaying(p => !p)}
              aria-label={playing ? 'Pause replay' : 'Play replay'}
              className="rounded p-1 text-slate-400 hover:bg-white/10 hover:text-slate-100"
            >
              {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            </button>
            <button
              type="button"
              onClick={() => { setPhase(0); setPlaying(true) }}
              aria-label="Restart replay"
              className="rounded p-1 text-slate-400 hover:bg-white/10 hover:text-slate-100"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <div className="rounded-md border border-white/5 bg-black/30">
          <p className="border-b border-white/5 px-3 py-1 font-mono text-[10px] uppercase tracking-[0.08em] text-slate-500">
            {current.queryLabel}
          </p>
          <pre className="overflow-x-auto px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-300 min-h-[6.2em]">
            {current.query}
          </pre>
        </div>
        <div className="flex gap-1" role="tablist" aria-label="Investigation steps">
          {PHASES.map((p, i) => (
            <button
              key={p.tool}
              type="button"
              role="tab"
              aria-selected={i === phase}
              aria-label={`Show step: ${p.tool}`}
              onClick={() => { setPhase(i); setPlaying(false) }}
              className="group flex-1 py-1.5"
            >
              <span className={`block h-1 rounded-full transition-colors ${
                i < phase ? 'bg-teal-500/60' : i === phase ? 'bg-teal-300' : 'bg-white/10 group-hover:bg-white/20'
              }`} />
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
