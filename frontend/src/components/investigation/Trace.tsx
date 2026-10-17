import { useState } from 'react'
import {
  AlertTriangle, Bot, Box, Brain, ChevronDown, ChevronRight, Crosshair, Database, FlaskConical, GitBranch, GitCommit,
  GitMerge, Network, Play, Search, ShieldCheck, TestTube, Users, Wrench,
} from 'lucide-react'
import type { AnalysisStep } from '../../services/api'
import { Badge } from '../ui/Badge'

const STEP_ICONS: Record<string, any> = {
  parse_bug_report: Search, match_stack_frames: Search, match_identifiers: Search, search_code: Search,
  get_callers: GitBranch, get_callees: GitBranch, get_tests: TestTube, recent_changes: GitCommit,
  graph_algorithms: Network, recall_past_investigations: Brain, rank_suspects: AlertTriangle,
  verdict: ShieldCheck, blast_radius: Network, find_experts: Users, generate_fix: Wrench, save_memory: Brain,
  code_history: GitMerge, who_owns: Users,
  select_tests: Crosshair, prepare_sandbox: Box, write_regression_test: FlaskConical, baseline_run: Play,
  apply_patch: Wrench, patched_run: Play, judge: ShieldCheck, save_to_graph: Database,
}

export function StepCard({ step }: { step: AnalysisStep }) {
  const [open, setOpen] = useState(false)
  const Icon = STEP_ICONS[step.action.replace(/^llm_/, '')] || Search
  const isLLM = step.actor === 'llm'
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div className={`h-8 w-8 rounded-full flex items-center justify-center shrink-0 ${isLLM ? 'bg-violet-100 text-violet-700 dark:bg-violet-500/15 dark:text-violet-300' : 'bg-primary/10 text-primary'}`}>
          {isLLM ? <Bot className="h-4 w-4" /> : <Icon className="h-4 w-4" />}
        </div>
        <div className="w-px flex-1 bg-border mt-1" />
      </div>
      <div className="flex-1 min-w-0 pb-5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-muted-foreground tabular-nums">#{step.step}</span>
          <h4 className="font-medium text-sm">{step.action.replace(/^llm_/, '').replace(/_/g, ' ')}</h4>
          {isLLM && <Badge variant="outline" className="text-violet-700 border-violet-300 dark:text-violet-300 dark:border-violet-500/50 text-[10px]">LLM chose this</Badge>}
          {step.actor === 'sandbox' && <Badge variant="outline" className="text-emerald-700 border-emerald-300 dark:text-emerald-300 dark:border-emerald-500/50 text-[10px]">sandbox</Badge>}
          {step.tool && !isLLM && <Badge variant="outline" className="text-[10px]">tool: {step.tool}</Badge>}
        </div>
        <p className="text-sm mt-1">{step.result_summary}</p>
        <p className="text-xs text-muted-foreground mt-1">{step.reasoning}</p>
        {step.query && (
          <button onClick={() => setOpen(!open)} className="mt-2 text-xs text-primary flex items-center gap-1">
            {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Cypher
          </button>
        )}
        {open && (
          <pre className="mt-2 p-3 bg-sidebar text-sidebar-foreground rounded-md font-mono text-[11px] overflow-x-auto whitespace-pre">{step.query}</pre>
        )}
      </div>
    </div>
  )
}

export function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="font-mono text-xs bg-sidebar rounded-lg p-4 overflow-x-auto">
      {diff.split('\n').map((line, i) => {
        const color = line.startsWith('+') && !line.startsWith('+++') ? 'text-green-400'
          : line.startsWith('-') && !line.startsWith('---') ? 'text-red-400'
          : line.startsWith('@@') ? 'text-sky-400' : 'text-slate-300'
        return <div key={i} className={color}>{line || ' '}</div>
      })}
    </pre>
  )
}
