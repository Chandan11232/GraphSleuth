import { useEffect, useMemo, useRef } from 'react'

/*
 * A graph drawn in real perspective on a 2D canvas: nodes hang above a floor grid, each with a stem down to it,
 * so depth reads without shading. Drag to orbit. No WebGL and no dependencies; text stays crisp at any size.
 * Always drawn on ink, so it looks the same in the light and dark themes.
 */

export type Vec3 = [number, number, number]

export interface G3Node {
  id: string
  label: string
  kind: string
  /** Fixed position. When any node lacks one, the whole graph is laid out by a force simulation. */
  pos?: Vec3
  /** Small text after the label, e.g. a score. */
  tag?: string
  root?: boolean
}

export interface G3Edge {
  id: string
  source: string
  target: string
  type: string
}

interface Props {
  nodes: G3Node[]
  edges: G3Edge[]
  /** Nodes and edges to show at full strength; the rest are dimmed. Omit to show everything. */
  lit?: Set<string>
  litEdges?: Set<string>
  focusId?: string | null
  onSelect?: (id: string) => void
  ariaLabel: string
  className?: string
  /** 'front' hides labels on nodes at the back, for graphs too dense to label fully. */
  labels?: 'all' | 'front'
}

export const KIND_COLOR: Record<string, string> = {
  BugReport: '#ff6b57',
  Function: '#7fb4ff',
  Test: '#5fe0a6',
  Commit: '#ffc24a',
  Person: '#f08cf0',
  Team: '#f08cf0',
  Issue: '#ff9d6b',
  PullRequest: '#b79cff',
  Investigation: '#b79cff',
  Decision: '#c8f03c',
}
export const EDGE_COLOR: Record<string, string> = {
  MENTIONS: '#ff6b57',
  CALLS: '#7fb4ff',
  TESTS: '#5fe0a6',
  TOUCHED: '#ffc24a',
  AUTHORED: '#f08cf0',
}
const NEUTRAL = '#b9b4a6'
const ROOT = '#ff4d3d'
const PAPER = '236, 231, 219'

const CAMERA = 640
const SWAY = 0.42

export const kindColor = (kind: string) => KIND_COLOR[kind] ?? NEUTRAL

function hash(s: string) {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619)
  return (h >>> 0) / 4294967296
}

/** Force layout in three dimensions. Starts from a Fibonacci sphere, so the same graph always lands the same way. */
function layout(nodes: G3Node[], edges: G3Edge[]): Map<string, Vec3> {
  const out = new Map<string, Vec3>()
  if (nodes.every(n => n.pos)) {
    nodes.forEach(n => out.set(n.id, n.pos!))
    return out
  }
  const n = nodes.length
  const p: Vec3[] = nodes.map((_, i) => {
    const y = 1 - (2 * (i + 0.5)) / n
    const r = Math.sqrt(1 - y * y)
    const a = i * 2.39996323
    return [Math.cos(a) * r * 120, y * 120, Math.sin(a) * r * 120]
  })
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const links = edges
    .map(e => [index.get(e.source), index.get(e.target)] as const)
    .filter((l): l is readonly [number, number] => l[0] !== undefined && l[1] !== undefined)
  const k = 150 / Math.cbrt(Math.max(n, 2))
  for (let step = 0; step < 240; step++) {
    const f: Vec3[] = p.map(() => [0, 0, 0])
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const d: Vec3 = [p[i][0] - p[j][0], p[i][1] - p[j][1], p[i][2] - p[j][2]]
        const len = Math.hypot(d[0], d[1], d[2]) || 0.01
        const push = (k * k) / (len * len)
        for (let a = 0; a < 3; a++) {
          f[i][a] += d[a] * push
          f[j][a] -= d[a] * push
        }
      }
    }
    for (const [i, j] of links) {
      const d: Vec3 = [p[i][0] - p[j][0], p[i][1] - p[j][1], p[i][2] - p[j][2]]
      const pull = Math.hypot(d[0], d[1], d[2]) / k
      for (let a = 0; a < 3; a++) {
        f[i][a] -= d[a] * pull
        f[j][a] += d[a] * pull
      }
    }
    const heat = 14 * (1 - step / 240)
    for (let i = 0; i < n; i++) {
      const len = Math.hypot(f[i][0], f[i][1], f[i][2]) || 0.01
      const move = Math.min(len, heat)
      for (let a = 0; a < 3; a++) p[i][a] += (f[i][a] / len) * move - p[i][a] * 0.012
    }
  }
  // Fit inside the same box the hand-placed graphs use, flattened a little so labels have room sideways.
  const reach = Math.max(...p.map(v => Math.hypot(v[0], v[1] * 1.3, v[2])), 1)
  const scale = 235 / reach
  nodes.forEach((node, i) => out.set(node.id, [p[i][0] * scale, p[i][1] * scale * 0.8, p[i][2] * scale]))
  return out
}

interface Shown { id: string; x: number; y: number; r: number; depth: number; fx: number; fy: number }

export function Graph3D({ nodes, edges, lit, litEdges, focusId, onSelect, ariaLabel, className, labels = 'all' }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const positions = useMemo(() => layout(nodes, edges), [nodes, edges])
  const live = useRef({ nodes, edges, lit, litEdges, focusId, onSelect, labels, positions })
  live.current = { nodes, edges, lit, litEdges, focusId, onSelect, labels, positions }

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const view = { yaw: -0.5, pitch: 0.3, w: 0, h: 0 }
    const pointer = { down: false, moved: 0, x: 0, y: 0, hover: null as string | null }
    const level = new Map<string, number>()
    const drawn = new Map<string, number>()
    let shown: Shown[] = []
    let frame = 0
    let visible = true
    let last = performance.now()
    let clock = 0

    const resize = () => {
      const box = canvas.getBoundingClientRect()
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      view.w = box.width
      view.h = box.height
      canvas.width = Math.round(box.width * dpr)
      canvas.height = Math.round(box.height * dpr)
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }

    const draw = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05)
      last = now
      if (!pointer.down && !pointer.hover) clock += dt
      const { nodes, edges, lit, litEdges, focusId, labels, positions } = live.current
      const { w, h } = view
      if (!w || !h) return

      const yaw = view.yaw + (still ? 0 : Math.sin(clock * 0.3) * SWAY)
      const cy = Math.cos(yaw), sy = Math.sin(yaw)
      const cp = Math.cos(view.pitch), sp = Math.sin(view.pitch)
      const unit = Math.min(w / 520, h / 400)
      const floor = 190
      const project = (x: number, y: number, z: number) => {
        const x1 = x * cy + z * sy
        const z1 = -x * sy + z * cy
        const y2 = y * cp - z1 * sp
        const z2 = y * sp + z1 * cp
        const s = (unit * CAMERA) / (CAMERA + z2)
        return { x: w / 2 + x1 * s, y: h * 0.47 + y2 * s, s: s / unit, depth: z2 }
      }

      ctx.clearRect(0, 0, w, h)
      ctx.lineCap = 'round'

      // Floor grid, fading towards its edge.
      const span = 300
      const cell = 60
      ctx.lineWidth = 1
      for (let a = -span; a <= span; a += cell) {
        for (let b = -span; b < span; b += cell) {
          const fade = 1 - Math.hypot(a, b + cell / 2) / (span * 1.25)
          if (fade <= 0) continue
          ctx.strokeStyle = `rgba(${PAPER}, ${0.11 * fade})`
          for (const [p, q] of [[project(a, floor, b), project(a, floor, b + cell)], [project(b, floor, a), project(b + cell, floor, a)]]) {
            ctx.beginPath()
            ctx.moveTo(p.x, p.y)
            ctx.lineTo(q.x, q.y)
            ctx.stroke()
          }
        }
      }

      const at = new Map<string, ReturnType<typeof project>>()
      shown = []
      for (const n of nodes) {
        const v = positions.get(n.id)
        if (!v) continue
        const p = project(v[0], v[1], v[2])
        const f = project(v[0], floor, v[2])
        at.set(n.id, p)
        const want = !lit || lit.has(n.id) ? 1 : 0
        const have = level.get(n.id) ?? want
        level.set(n.id, still ? want : have + (want - have) * Math.min(1, dt * 5))
        shown.push({ id: n.id, x: p.x, y: p.y, r: 6.5 * p.s, depth: p.depth, fx: f.x, fy: f.y })
      }

      // Stems and the dot each node casts on the floor.
      for (const s of shown) {
        const a = 0.05 + 0.13 * (level.get(s.id) ?? 1)
        ctx.strokeStyle = `rgba(${PAPER}, ${a})`
        ctx.setLineDash([2, 4])
        ctx.beginPath()
        ctx.moveTo(s.x, s.y)
        ctx.lineTo(s.fx, s.fy)
        ctx.stroke()
        ctx.setLineDash([])
        ctx.fillStyle = `rgba(${PAPER}, ${a * 1.6})`
        ctx.beginPath()
        ctx.ellipse(s.fx, s.fy, 3.2, 1.5, 0, 0, Math.PI * 2)
        ctx.fill()
      }

      for (const e of edges) {
        const a = at.get(e.source)
        const b = at.get(e.target)
        if (!a || !b) continue
        const want = !litEdges || litEdges.has(e.id) ? 1 : 0
        const have = drawn.get(e.id) ?? want
        const t = still ? want : have + (want - have) * Math.min(1, dt * 2.6)
        drawn.set(e.id, t)
        const color = EDGE_COLOR[e.type] ?? NEUTRAL
        const touches = pointer.hover === e.source || pointer.hover === e.target
        const dx = b.x - a.x, dy = b.y - a.y
        const len = Math.hypot(dx, dy) || 1
        // Stop short of the target so the arrowhead meets the node's edge, not its centre.
        const end = Math.max(0, 1 - (7.5 * b.s + 5) / len)
        ctx.strokeStyle = color
        ctx.lineWidth = touches ? 2 : 1.3
        if (t < 0.999) {
          ctx.globalAlpha = 0.12
          ctx.beginPath()
          ctx.moveTo(a.x, a.y)
          ctx.lineTo(a.x + dx * end, a.y + dy * end)
          ctx.stroke()
        }
        if (t > 0.01) {
          ctx.globalAlpha = touches ? 1 : 0.78
          const tx = a.x + dx * end * t, ty = a.y + dy * end * t
          ctx.beginPath()
          ctx.moveTo(a.x, a.y)
          ctx.lineTo(tx, ty)
          ctx.stroke()
          const ux = dx / len, uy = dy / len
          const head = 5.5 * b.s
          ctx.fillStyle = color
          ctx.beginPath()
          ctx.moveTo(tx + ux * head, ty + uy * head)
          ctx.lineTo(tx - uy * head * 0.55, ty + ux * head * 0.55)
          ctx.lineTo(tx + uy * head * 0.55, ty - ux * head * 0.55)
          ctx.fill()
          if (!still && t > 0.95) {
            // One hop travelling along the edge, in the direction of the relationship.
            const u = (clock * 0.32 + hash(e.id)) % 1
            ctx.globalAlpha = Math.sin(u * Math.PI)
            ctx.beginPath()
            ctx.arc(a.x + dx * end * u, a.y + dy * end * u, 2.1, 0, Math.PI * 2)
            ctx.fill()
          }
        }
        ctx.globalAlpha = 1
      }

      shown.sort((p, q) => q.depth - p.depth)
      const byId = new Map(nodes.map(n => [n.id, n]))
      ctx.textBaseline = 'middle'
      const nearness = (depth: number) => Math.max(0, Math.min(1, 1 - (depth + 160) / 520))

      // Give out label space nearest-first, after the nodes that must be named; a label that would cover one
      // already placed is left off until the graph turns.
      const placed: { x: number; y: number; w: number; h: number }[] = []
      const label = new Map<string, { x: number; text: string; size: number }>()
      const must = (id: string) => (byId.get(id)!.root || id === focusId || id === pointer.hover ? 1 : 0)
      for (const s of [...shown].sort((p, q) => must(q.id) - must(p.id) || p.depth - q.depth)) {
        const n = byId.get(s.id)!
        const near = nearness(s.depth)
        if (labels === 'front' && near < 0.35 && !must(s.id)) continue
        const size = Math.max(10, Math.min(13, 8 + 4.6 * near))
        ctx.font = `${n.root || s.id === focusId ? 600 : 500} ${size}px "IBM Plex Mono", ui-monospace, monospace`
        const text = n.label.length > 30 ? `${n.label.slice(0, 29)}…` : n.label
        const width = ctx.measureText(text).width
        const gap = s.r + (n.root || s.id === focusId ? 15 : 9)
        const left = s.x + gap + width > w - 6
        const x = left ? s.x - gap - width : s.x + gap
        const box = { x: x - 4, y: s.y - size * 0.8, w: width + 8, h: size * 1.6 }
        const clash = placed.some(b => box.x < b.x + b.w && b.x < box.x + box.w && box.y < b.y + b.h && b.y < box.y + box.h)
        if (clash && !must(s.id)) continue
        placed.push(box)
        label.set(s.id, { x, text, size })
      }

      for (const s of shown) {
        const n = byId.get(s.id)!
        const on = level.get(s.id) ?? 1
        const hovered = pointer.hover === s.id
        const color = n.root ? ROOT : kindColor(n.kind)
        const near = nearness(s.depth)
        const alpha = (0.22 + 0.78 * on) * (0.55 + 0.45 * near)

        if (n.root && !still) {
          const beat = (clock * 0.9) % 1
          ctx.globalAlpha = (1 - beat) * 0.8
          ctx.strokeStyle = ROOT
          ctx.lineWidth = 1.5
          ctx.beginPath()
          ctx.arc(s.x, s.y, s.r + 4 + beat * 16, 0, Math.PI * 2)
          ctx.stroke()
        }
        if (n.root || s.id === focusId || hovered) {
          ctx.globalAlpha = 1
          ctx.strokeStyle = n.root ? ROOT : `rgb(${PAPER})`
          ctx.lineWidth = 1
          ctx.beginPath()
          ctx.arc(s.x, s.y, s.r + 4.5, 0, Math.PI * 2)
          ctx.stroke()
        }

        ctx.globalAlpha = alpha
        ctx.fillStyle = '#0f1412'
        ctx.strokeStyle = color
        ctx.lineWidth = 1.6
        ctx.beginPath()
        ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2)
        ctx.fill()
        ctx.stroke()
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(s.x, s.y, s.r * (0.3 + 0.28 * on), 0, Math.PI * 2)
        ctx.fill()

        const tag = label.get(s.id)
        if (!tag) continue
        const { x, text, size } = tag
        ctx.font = `${n.root || s.id === focusId ? 600 : 500} ${size}px "IBM Plex Mono", ui-monospace, monospace`
        ctx.globalAlpha = hovered ? 1 : alpha
        ctx.fillStyle = `rgb(${PAPER})`
        ctx.fillText(text, x, s.y + 0.5)
        ctx.font = `500 9px "IBM Plex Mono", ui-monospace, monospace`
        if (n.root) {
          ctx.fillStyle = ROOT
          ctx.fillText(`ROOT CAUSE${n.tag ? ` · ${n.tag}` : ''}`, x, s.y - size)
        } else if (n.tag) {
          ctx.fillStyle = color
          ctx.fillText(n.tag, x, s.y - size)
        }
      }
      ctx.globalAlpha = 1
    }

    const tick = (now: number) => {
      draw(now)
      frame = visible ? requestAnimationFrame(tick) : 0
    }
    const wake = () => {
      if (!frame && visible) {
        last = performance.now()
        frame = requestAnimationFrame(tick)
      }
    }

    const nodeAt = (x: number, y: number) => {
      let best: string | null = null
      let reach = 16
      for (const s of shown) {
        const d = Math.hypot(s.x - x, s.y - y)
        if (d < Math.max(reach, s.r + 4)) {
          reach = d
          best = s.id
        }
      }
      return best
    }
    const local = (e: PointerEvent) => {
      const box = canvas.getBoundingClientRect()
      return [e.clientX - box.left, e.clientY - box.top] as const
    }
    const down = (e: PointerEvent) => {
      pointer.down = true
      pointer.moved = 0
      pointer.x = e.clientX
      pointer.y = e.clientY
      canvas.setPointerCapture(e.pointerId)
    }
    const move = (e: PointerEvent) => {
      if (pointer.down) {
        const dx = e.clientX - pointer.x, dy = e.clientY - pointer.y
        pointer.moved += Math.abs(dx) + Math.abs(dy)
        pointer.x = e.clientX
        pointer.y = e.clientY
        view.yaw += dx * 0.007
        view.pitch = Math.max(-0.25, Math.min(1.1, view.pitch + dy * 0.005))
        canvas.style.cursor = 'grabbing'
      } else {
        const [x, y] = local(e)
        pointer.hover = nodeAt(x, y)
        canvas.style.cursor = pointer.hover && live.current.onSelect ? 'pointer' : 'grab'
      }
    }
    const up = (e: PointerEvent) => {
      if (!pointer.down) return
      pointer.down = false
      canvas.style.cursor = 'grab'
      if (pointer.moved < 5) {
        const [x, y] = local(e)
        const hit = nodeAt(x, y)
        if (hit) live.current.onSelect?.(hit)
      }
    }
    const leave = () => {
      pointer.hover = null
    }

    resize()
    const sizes = new ResizeObserver(resize)
    sizes.observe(canvas)
    const seen = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting
      wake()
    })
    seen.observe(canvas)
    canvas.addEventListener('pointerdown', down)
    canvas.addEventListener('pointermove', move)
    canvas.addEventListener('pointerup', up)
    canvas.addEventListener('pointercancel', up)
    canvas.addEventListener('pointerleave', leave)
    wake()

    return () => {
      cancelAnimationFrame(frame)
      sizes.disconnect()
      seen.disconnect()
      canvas.removeEventListener('pointerdown', down)
      canvas.removeEventListener('pointermove', move)
      canvas.removeEventListener('pointerup', up)
      canvas.removeEventListener('pointercancel', up)
      canvas.removeEventListener('pointerleave', leave)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      role="img"
      aria-label={ariaLabel}
      className={`block h-full w-full cursor-grab touch-pan-y select-none ${className ?? ''}`}
    />
  )
}
