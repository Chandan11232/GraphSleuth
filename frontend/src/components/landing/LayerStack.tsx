import { useState } from 'react'
import { kindColor } from '../three/Graph3D'

/*
 * The graph model as four stacked planes: code at the bottom, then history, the team around it, and the agent's
 * memory on top. Uprights are relationships that cross from one plane to another. Pick a plane to read its patterns.
 */

type Pattern = [from: string, rel: string, to: string]

interface Layer {
  name: string
  blurb: string
  dots: { label: string; x: number; y: number }[]
  patterns: Pattern[]
}

// Bottom plane first.
const LAYERS: Layer[] = [
  {
    name: 'Code',
    blurb: 'What tree-sitter parses out of the repository.',
    dots: [
      { label: 'Function', x: 62, y: 60 }, { label: 'Test', x: 30, y: 26 }, { label: 'File', x: 26, y: 74 },
      { label: 'Class', x: 76, y: 26 },
    ],
    patterns: [
      ['Function', 'CALLS', 'Function'], ['Test', 'TESTS', 'Function'], ['File', 'CONTAINS', 'Function'],
      ['File', 'IMPORTS', 'File'], ['Class', 'HAS_METHOD', 'Function'],
    ],
  },
  {
    name: 'History',
    blurb: 'The last 300 commits, mapped to the function line spans each diff touched.',
    dots: [{ label: 'Commit', x: 62, y: 60 }, { label: 'Person', x: 30, y: 34 }],
    patterns: [['Person', 'AUTHORED', 'Commit'], ['Commit', 'TOUCHED', 'Function'], ['Commit', 'MODIFIES', 'File']],
  },
  {
    name: 'Team',
    blurb: 'Issues, pull requests, reviews, decisions and CODEOWNERS.',
    dots: [
      { label: 'PullRequest', x: 62, y: 60 }, { label: 'Issue', x: 78, y: 24 }, { label: 'Decision', x: 34, y: 22 },
      { label: 'Team', x: 26, y: 74 },
    ],
    patterns: [
      ['PullRequest', 'INCLUDES', 'Commit'], ['PullRequest', 'FIXES', 'Issue'], ['Person', 'REVIEWED', 'PullRequest'],
      ['Decision', 'DECIDED_IN', 'Comment'], ['Team', 'OWNS', 'File'], ['Person', 'MEMBER_OF', 'Team'],
    ],
  },
  {
    name: 'Memory',
    blurb: 'Every investigation, linked to what it suspected and what was confirmed.',
    dots: [{ label: 'Investigation', x: 62, y: 60 }, { label: 'BugReport', x: 30, y: 36 }],
    patterns: [
      ['BugReport', 'MENTIONS', 'Function'], ['BugReport', 'INVESTIGATED_IN', 'Investigation'],
      ['Investigation', 'SUSPECTS', 'Function'], ['Investigation', 'CONFIRMED', 'Function'],
    ],
  },
]

// Relationships that cross planes: [upper layer, lower layer, x, y].
const UPRIGHTS: [number, number, number, number][] = [
  [3, 0, 62, 60],
  [2, 0, 26, 74],
]

const SIDE = 250
const GAP = 78
const LIFT = 26

export function LayerStack() {
  const [active, setActive] = useState(3)
  const z = (i: number) => i * GAP + (i === active ? LIFT : 0)

  return (
    <div className="grid grid-cols-1 items-center gap-x-12 gap-y-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
      <div className="relative flex h-[440px] items-center justify-center overflow-hidden sm:h-[520px]" style={{ perspective: 1600 }} aria-hidden="true">
        <div
          className="relative max-sm:scale-[0.78]"
          style={{
            width: SIDE, height: SIDE, transformStyle: 'preserve-3d',
            transform: 'translateY(110px) rotateX(58deg) rotateZ(-42deg)',
          }}
        >
          {LAYERS.map((layer, i) => {
            const on = i === active
            return (
              <div
                key={layer.name}
                onMouseEnter={() => setActive(i)}
                onClick={() => setActive(i)}
                className={`absolute inset-0 cursor-pointer border transition-[transform,background-color,border-color] duration-500 ease-out ${
                  on ? 'border-primary bg-accent/70' : 'border-foreground/25 bg-card/70'
                }`}
                style={{
                  transform: `translateZ(${z(i)}px)`,
                  backgroundImage:
                    'linear-gradient(hsl(var(--foreground) / 0.07) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--foreground) / 0.07) 1px, transparent 1px)',
                  backgroundSize: '25px 25px',
                }}
              >
                <span className={`absolute bottom-2 left-2.5 font-mono text-[12px] font-semibold uppercase tracking-[0.12em] ${on ? '' : 'text-muted-foreground'}`}>
                  0{i + 1} {layer.name}
                </span>
                {layer.dots.map(d => (
                  <span key={d.label} className="absolute flex items-center gap-1.5" style={{ left: `${d.x}%`, top: `${d.y}%`, transform: 'translate(-5px, -50%)' }}>
                    <span className="h-2.5 w-2.5 rounded-full border border-foreground/60" style={{ background: kindColor(d.label) }} />
                    <span className="font-mono text-[10.5px]">{d.label}</span>
                  </span>
                ))}
              </div>
            )
          })}
          {UPRIGHTS.map(([top, bottom, x, y]) => (
            <span
              key={`${top}${bottom}`}
              className="pointer-events-none absolute w-0 border-l border-dashed border-foreground/50 transition-[transform,height] duration-500 ease-out"
              style={{
                left: `${x}%`, top: `${y}%`, height: z(top) - z(bottom), transformOrigin: 'top',
                transform: `translateZ(${z(top)}px) rotateX(-90deg)`,
              }}
            />
          ))}
        </div>
      </div>

      <div>
        <div className="flex border-b" role="tablist" aria-label="Graph layers">
          {LAYERS.map((layer, i) => (
            <button
              key={layer.name}
              type="button"
              role="tab"
              aria-selected={i === active}
              onClick={() => setActive(i)}
              className={`-mb-px border-b-2 px-3 py-2.5 font-mono text-xs uppercase tracking-[0.1em] transition-colors first:pl-0 ${
                i === active ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'
              }`}
            >
              {layer.name}
            </button>
          ))}
        </div>
        <div role="tabpanel" className="min-h-[15rem] space-y-5 pt-6">
          <p className="max-w-md text-muted-foreground">{LAYERS[active].blurb}</p>
          <ul className="space-y-2 overflow-x-auto">
            {LAYERS[active].patterns.map(([from, rel, to]) => (
              <li key={`${from}${rel}${to}`} className="whitespace-nowrap font-mono text-[13.5px]">
                <span className="text-muted-foreground">(</span>
                {from}
                <span className="text-muted-foreground">)-[:</span>
                <span className="bg-accent px-1 font-semibold text-accent-foreground">{rel}</span>
                <span className="text-muted-foreground">]-&gt;(</span>
                {to}
                <span className="text-muted-foreground">)</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
