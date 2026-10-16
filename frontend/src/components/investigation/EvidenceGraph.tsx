import { useMemo, useState } from 'react'
import dagre from '@dagrejs/dagre'
import ReactFlow, { Background, Controls, Handle, MarkerType, Position, type Edge, type Node, type NodeProps } from 'reactflow'
import 'reactflow/dist/style.css'
import type { GraphResponse, GraphNode } from '../../services/api'

const TYPE_STYLE: Record<string, { bg: string; border: string; tag: string }> = {
  BugReport: { bg: '#fef2f2', border: '#f87171', tag: 'Bug' },
  Investigation: { bg: '#f5f3ff', border: '#a78bfa', tag: 'Investigation' },
  Function: { bg: '#eff6ff', border: '#93c5fd', tag: 'Function' },
  Test: { bg: '#f0fdf4', border: '#86efac', tag: 'Test' },
  Commit: { bg: '#fffbeb', border: '#fcd34d', tag: 'Commit' },
  Person: { bg: '#fdf4ff', border: '#e879f9', tag: 'Person' },
}

const EDGE_COLOR: Record<string, string> = {
  CALLS: '#3b82f6', TESTS: '#22c55e', TOUCHED: '#f59e0b', AUTHORED: '#d946ef', MENTIONS: '#ef4444',
  SUSPECTS: '#8b5cf6', CONFIRMED: '#16a34a', INVESTIGATED_IN: '#a78bfa',
}

function GraphCard({ data }: NodeProps<GraphNode>) {
  const style = TYPE_STYLE[data.type] || TYPE_STYLE.Function
  const p = data.properties
  const root = p.root_cause
  return (
    <div
      className="rounded-lg px-3 py-2 shadow-sm text-left"
      style={{
        width: 200, background: style.bg, border: `${root ? 3 : 1.5}px solid ${root ? '#dc2626' : style.border}`,
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="flex items-center justify-between gap-1">
        <span className="text-[10px] uppercase tracking-wide text-gray-500">{root ? 'Root cause' : style.tag}</span>
        {p.rank && <span className="text-[10px] font-mono rounded bg-white/70 px-1">#{p.rank} · {p.suspicion_score?.toFixed(2)}</span>}
      </div>
      <p className="text-xs font-semibold text-gray-900 truncate" title={data.label}>{data.label}</p>
      {(p.file_path || p.email) && <p className="text-[10px] text-gray-500 truncate">{p.file_path || p.email}</p>}
      {data.type === 'Commit' && p.author && <p className="text-[10px] text-gray-500 truncate">{p.author}</p>}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  )
}

const nodeTypes = { card: GraphCard }

function layout(graph: GraphResponse, hidden: Set<string>) {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', nodesep: 18, ranksep: 70 })
  g.setDefaultEdgeLabel(() => ({}))
  const visible = graph.nodes.filter(n => !hidden.has(n.type))
  const ids = new Set(visible.map(n => n.id))
  visible.forEach(n => g.setNode(n.id, { width: 200, height: 62 }))
  const edges = graph.edges.filter(e => ids.has(e.source) && ids.has(e.target))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)
  const nodes: Node[] = visible.map(n => {
    const pos = g.node(n.id)
    return { id: n.id, type: 'card', data: n, position: { x: pos.x - 100, y: pos.y - 31 } }
  })
  const flowEdges: Edge[] = edges.map(e => ({
    id: e.id,
    source: e.source,
    target: e.target,
    label: e.type,
    labelStyle: { fontSize: 9, fill: '#6b7280' },
    labelBgStyle: { fillOpacity: 0.8 },
    style: { stroke: EDGE_COLOR[e.type] || '#9ca3af', strokeWidth: e.type === 'CONFIRMED' ? 2.5 : 1.3 },
    markerEnd: { type: MarkerType.ArrowClosed, color: EDGE_COLOR[e.type] || '#9ca3af' },
    animated: e.type === 'CALLS',
  }))
  return { nodes, edges: flowEdges }
}

export function EvidenceGraph({ graph, onSelect }: { graph: GraphResponse; onSelect?: (n: GraphNode) => void }) {
  const [hidden, setHidden] = useState<Set<string>>(new Set(['Person']))
  const types = useMemo(() => Array.from(new Set(graph.nodes.map(n => n.type))), [graph])
  const { nodes, edges } = useMemo(() => layout(graph, hidden), [graph, hidden])

  const toggle = (t: string) => setHidden(prev => {
    const next = new Set(prev)
    if (next.has(t)) next.delete(t)
    else next.add(t)
    return next
  })

  return (
    <div className="h-full flex flex-col">
      <div className="flex flex-wrap gap-2 px-4 py-2 border-b text-xs">
        {types.map(t => {
          const s = TYPE_STYLE[t] || TYPE_STYLE.Function
          const count = graph.nodes.filter(n => n.type === t).length
          return (
            <button key={t} onClick={() => toggle(t)}
                    className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 ${hidden.has(t) ? 'opacity-40' : ''}`}>
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: s.border }} /> {t} {count}
            </button>
          )
        })}
        <span className="text-muted-foreground ml-auto self-center">click a type to hide it · the red-bordered node is the verdict</span>
      </div>
      <div className="flex-1 min-h-0">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_, n) => onSelect?.(n.data as GraphNode)}
          fitView
          minZoom={0.2}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={16} color="#e5e7eb" />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  )
}
