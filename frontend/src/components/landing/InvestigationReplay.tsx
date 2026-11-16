import { useEffect, useMemo, useState } from 'react'
import { Pause, Play, RotateCcw } from 'lucide-react'
import { EDGE_COLOR, Graph3D, type G3Edge, type G3Node, type Vec3 } from '../three/Graph3D'

/*
 * A replay of a real investigation on the bundled demo repo (acme-shop): the bug report
 * "Refund of unknown order returns 500". Step summaries, scores and Cypher are copied from
 * that run; the graph lights the nodes and edges each step touched.
 */

type NodeId = 'bug' | 'handle' | 'process' | 'validate' | 'test' | 'find' | 'notify' | 'commit' | 'dana'

/* Placed by hand: the bug report on the left, the three stack frames down the middle, what each one reaches behind and in front. */
const PLACED: { id: NodeId; kind: string; label: string; pos: Vec3 }[] = [
  { id: 'bug', kind: 'BugReport', label: 'Refund of unknown order', pos: [-215, 15, 40] },
  { id: 'handle', kind: 'Function', label: 'handle_refund_request', pos: [-55, -100, -40] },
  { id: 'process', kind: 'Function', label: 'process_refund', pos: [-15, 5, 0] },
  { id: 'validate', kind: 'Function', label: 'validate_refund_amount', pos: [-55, 110, -50] },
  { id: 'test', kind: 'Test', label: 'test_full_refund_marks…', pos: [110, -145, 95] },
  { id: 'find', kind: 'Function', label: 'find_order', pos: [150, -60, -95] },
  { id: 'notify', kind: 'Function', label: 'notify_refund', pos: [170, 25, 75] },
  { id: 'commit', kind: 'Commit', label: 'Refactor refund flow…', pos: [105, 100, -115] },
  { id: 'dana', kind: 'Person', label: 'Dana Kim', pos: [215, 140, -25] },
]

const EDGES: G3Edge[] = [
  { id: 'm1', source: 'bug', target: 'handle', type: 'MENTIONS' },
  { id: 'm2', source: 'bug', target: 'process', type: 'MENTIONS' },
  { id: 'm3', source: 'bug', target: 'validate', type: 'MENTIONS' },
  { id: 'c1', source: 'handle', target: 'process', type: 'CALLS' },
  { id: 'c2', source: 'process', target: 'validate', type: 'CALLS' },
  { id: 'c3', source: 'process', target: 'find', type: 'CALLS' },
  { id: 'c4', source: 'process', target: 'notify', type: 'CALLS' },
  { id: 't1', source: 'test', target: 'process', type: 'TESTS' },
  { id: 'r1', source: 'commit', target: 'process', type: 'TOUCHED' },
  { id: 'a1', source: 'dana', target: 'commit', type: 'AUTHORED' },
]

const EDGE_TYPES = ['MENTIONS', 'CALLS', 'TESTS', 'TOUCHED', 'AUTHORED']

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

  const lit = useMemo(() => new Set<string>(PHASES.slice(0, phase + 1).flatMap(p => p.nodes)), [phase])
  const litEdges = useMemo(() => new Set<string>(PHASES.slice(0, phase + 1).flatMap(p => p.edges)), [phase])
  const current = PHASES[phase]
  const ranked = phase >= 4
  const solved = phase >= 5
  const nodes = useMemo<G3Node[]>(
    () => PLACED.map(n => ({
      ...n,
      tag: n.id === 'process' && solved ? '79%' : ranked ? SCORES[n.id] : undefined,
      root: n.id === 'process' && solved,
    })),
    [ranked, solved],
  )

  return (
    <div className="relative">
      <div className="flex items-center justify-between gap-3 font-mono text-[11px] text-white/50">
        <p className="truncate">
          acme-shop <span className="text-white/25">/</span>{' '}
          <span className="text-white/85">Refund of unknown order returns 500</span>
        </p>
        <span className={`shrink-0 tabular-nums ${solved ? 'text-[#ff6b57]' : 'text-signal'}`}>
          {solved ? 'root cause found' : `step ${current.step}`}
        </span>
      </div>

      <div className="relative mt-2 h-[330px] border border-white/10 sm:h-[430px]">
        <Graph3D
          nodes={nodes}
          edges={EDGES}
          lit={lit}
          litEdges={litEdges}
          ariaLabel="Graph of the investigation: the bug report mentions three functions; process_refund is the root cause"
        />
        <p className="pointer-events-none absolute bottom-2.5 right-3 font-mono text-[10px] text-white/35">drag to orbit</p>
        <ul className="pointer-events-none absolute left-3 top-2.5 space-y-1 font-mono text-[10px] text-white/50">
          {EDGE_TYPES.map(t => (
            <li key={t} className="flex items-center gap-2">
              <span className="h-px w-4" style={{ background: EDGE_COLOR[t] }} /> {t}
            </li>
          ))}
        </ul>
      </div>

      <div className="border-x border-b border-white/10 bg-white/[0.03]">
        <div className="flex items-start justify-between gap-3 px-4 pt-3">
          <p className="min-h-[2.7em] text-[13px] leading-snug text-white/85">
            <span className="font-mono text-signal">{current.tool}</span>
            <span className="text-white/30"> → </span>
            {current.summary}
          </p>
          <div className="flex shrink-0 gap-1">
            <button
              type="button"
              onClick={() => setPlaying(p => !p)}
              aria-label={playing ? 'Pause replay' : 'Play replay'}
              className="p-1.5 text-white/50 hover:bg-white/10 hover:text-white"
            >
              {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            </button>
            <button
              type="button"
              onClick={() => { setPhase(0); setPlaying(true) }}
              aria-label="Restart replay"
              className="p-1.5 text-white/50 hover:bg-white/10 hover:text-white"
            >
              <RotateCcw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>
        <div className="flex gap-3 px-4 pb-1 pt-2">
          <span className="w-12 shrink-0 pt-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-white/35">
            {current.queryLabel}
          </span>
          <pre className="min-h-[6.4em] flex-1 overflow-x-auto font-mono text-[11px] leading-relaxed text-white/65">
            {current.query}
          </pre>
        </div>
        <div className="flex" role="tablist" aria-label="Investigation steps">
          {PHASES.map((p, i) => (
            <button
              key={p.tool}
              type="button"
              role="tab"
              aria-selected={i === phase}
              aria-label={`Show step: ${p.tool}`}
              onClick={() => { setPhase(i); setPlaying(false) }}
              className="group flex-1 pt-2"
            >
              <span className={`block h-[3px] transition-colors ${
                i < phase ? 'bg-signal/45' : i === phase ? 'bg-signal' : 'bg-white/10 group-hover:bg-white/25'
              }`} />
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
