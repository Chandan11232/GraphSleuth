import { useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, ArrowLeft, Bot, Brain, CheckCircle, ChevronDown, ChevronRight, FileCode, GitBranch, GitCommit,
  FlaskConical, GitMerge, Loader2, Network, Search, ShieldCheck, Sparkles, TestTube, Users, Wrench,
} from 'lucide-react'
import { useEvidenceGraph, useInvestigation } from '../hooks'
import { api, errorMessage, type AnalysisStep, type GraphNode, type Suspect } from '../services/api'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/Tabs'
import { EvidenceGraph } from '../components/investigation/EvidenceGraph'
import { SuggestTestDialog, type TestTarget } from '../components/SuggestTestDialog'
import { CodeHistoryView, OwnersView } from '../components/knowledge/CodeHistory'

const STEP_ICONS: Record<string, any> = {
  parse_bug_report: Search, match_stack_frames: Search, match_identifiers: Search, search_code: Search,
  get_callers: GitBranch, get_callees: GitBranch, get_tests: TestTube, recent_changes: GitCommit,
  graph_algorithms: Network, recall_past_investigations: Brain, rank_suspects: AlertTriangle,
  verdict: ShieldCheck, blast_radius: Network, find_experts: Users, generate_fix: Wrench, save_memory: Brain,
  code_history: GitMerge, who_owns: Users,
}

const FACTOR_LABEL: Record<string, string> = {
  relevance: 'Matches report', recent_change: 'Recently changed', untested: 'Untested',
  memory: 'Agent memory', complexity: 'Complexity', centrality: 'Centrality',
}

function StepCard({ step }: { step: AnalysisStep }) {
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

function ScoreBars({ s }: { s: Suspect }) {
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2">
      {Object.entries(s.breakdown).map(([k, v]) => (
        <div key={k} className="flex items-center gap-2 text-[11px]">
          <span className="w-28 text-muted-foreground shrink-0">{FACTOR_LABEL[k] || k}</span>
          <div className="flex-1 h-1.5 bg-muted rounded">
            <div className="h-1.5 rounded bg-primary" style={{ width: `${Math.round(v * 100)}%` }} />
          </div>
          <span className="w-8 text-right tabular-nums">{v.toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`

function DiffView({ diff }: { diff: string }) {
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

export function InvestigationView() {
  const { investigationId } = useParams<{ investigationId: string }>()
  const navigate = useNavigate()
  const { steps, result, status, message, error, reload } = useInvestigation(investigationId)
  const done = !!result && status !== 'running'
  const { graph, loading: graphLoading } = useEvidenceGraph(investigationId, done && !!result?.verdict?.id)
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'trace'
  const setTab = (t: string) => setParams({ tab: t }, { replace: true })
  const [selected, setSelected] = useState<GraphNode | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [testTarget, setTestTarget] = useState<TestTarget | null>(null)

  const confirm = async (s: Suspect) => {
    setConfirming(s.id)
    setConfirmError(null)
    try {
      await api.post(`/investigations/${investigationId}/confirm`, { function_id: s.id, note: 'confirmed in UI' })
      await reload()
    } catch (err) {
      setConfirmError(errorMessage(err, 'Could not save confirmation'))
    } finally {
      setConfirming(null)
    }
  }

  // An investigation that matched no code has an empty verdict and fix.
  const verdict = result?.verdict?.id ? result.verdict : undefined
  const fix = result?.fix?.target ? result.fix : undefined
  const suspects = result?.suspects ?? []
  const noMatch = done && !verdict
  const lastStep = steps[steps.length - 1]
  const confirmedIds = new Set((result?.confirmed || []).map(c => c.id))

  return (
    <div className="px-6 py-6 lg:px-8 space-y-5 max-w-7xl">
      <div className="flex items-start gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate(-1)}><ArrowLeft className="h-4 w-4" /></Button>
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-semibold truncate">{result?.title || 'Investigation'}</h1>
          <div className="flex items-center gap-2 text-sm text-muted-foreground flex-wrap">
            {result?.repository_name && <span>{result.repository_name}</span>}
            <Badge variant={status === 'failed' ? 'destructive' : status === 'running' ? 'secondary' : 'default'}>
              {status === 'running' && <Loader2 className="h-3 w-3 mr-1 animate-spin" />} {status}
            </Badge>
            {result?.mode && <Badge variant="outline">{result.mode === 'graph+llm' ? 'graph + LLM' : 'graph only'}</Badge>}
            <span>{steps.length} steps</span>
          </div>
        </div>
      </div>

      {error && <div className="p-3 rounded-lg bg-destructive/10 text-destructive text-sm">{error}</div>}

      {noMatch && status !== 'failed' && (
        <Card className="border-amber-200 bg-amber-50/50 dark:border-amber-500/30 dark:bg-amber-500/10">
          <CardContent className="p-4 flex gap-4 items-start">
            <AlertTriangle className="h-7 w-7 text-amber-600 dark:text-amber-400 shrink-0" />
            <div className="text-sm">
              <p className="font-medium">No matching code found</p>
              <p className="text-muted-foreground mt-1">
                {steps.find(s => s.action === 'no_match')?.reasoning || lastStep?.reasoning}
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {verdict && (
        <Card className="border-red-200 bg-red-50/40 dark:border-red-500/40 dark:bg-red-500/10">
          <CardContent className="p-4 flex gap-4 items-start">
            <ShieldCheck className="h-8 w-8 text-red-600 dark:text-red-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs uppercase tracking-wide text-red-700 dark:text-red-300">Root cause</span>
                <code className="font-semibold">{verdict.name}</code>
                <span className="text-xs text-muted-foreground">{verdict.file_path}</span>
                <Badge variant="outline">{Math.round(verdict.confidence * 100)}% confidence</Badge>
                <Badge variant="outline">{verdict.source === 'llm' ? 'LLM verdict' : 'graph verdict'}</Badge>
                {confirmedIds.size > 0 && <Badge className="bg-green-600"><CheckCircle className="h-3 w-3 mr-1" /> confirmed</Badge>}
              </div>
              <p className="text-sm mt-1">{verdict.explanation}</p>
              {fix?.cause && <p className="text-sm mt-1"><span className="font-medium">Defect:</span> {fix.cause}</p>}
            </div>
          </CardContent>
        </Card>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="trace"><Search className="h-4 w-4 mr-1.5" /> Reasoning trace</TabsTrigger>
          <TabsTrigger value="graph" disabled={!verdict}><Network className="h-4 w-4 mr-1.5" /> Evidence graph</TabsTrigger>
          <TabsTrigger value="suspects" disabled={!verdict}><AlertTriangle className="h-4 w-4 mr-1.5" /> Suspects</TabsTrigger>
          <TabsTrigger value="fix" disabled={!fix}><Wrench className="h-4 w-4 mr-1.5" /> Fix</TabsTrigger>
        </TabsList>

        <TabsContent value="trace" className="mt-4">
          <Card>
            <CardContent className="pt-6">
              {steps.map(s => <StepCard key={s.step} step={s} />)}
              {status === 'running' && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" /> {message || 'Investigating…'}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="graph" className="mt-4">
          <div className="grid gap-4 lg:grid-cols-4">
            <Card className="lg:col-span-3 overflow-hidden">
              <div className="h-[620px]">
                {graphLoading || !graph ? (
                  <div className="h-full flex items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div>
                ) : (
                  <EvidenceGraph graph={graph} onSelect={setSelected} />
                )}
              </div>
            </Card>
            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-sm">Node details</CardTitle></CardHeader>
              <CardContent className="text-xs space-y-1">
                {selected ? (
                  <>
                    <Badge variant="outline">{selected.type}</Badge>
                    <p className="font-semibold text-sm break-all">{selected.label}</p>
                    {Object.entries(selected.properties).filter(([k]) => k !== 'id').map(([k, v]) => (
                      <div key={k} className="flex gap-2">
                        <span className="text-muted-foreground w-24 shrink-0">{k}</span>
                        <span className="break-all">{k === 'timestamp' ? new Date(Number(v)).toLocaleDateString() : String(v)}</span>
                      </div>
                    ))}
                  </>
                ) : (
                  <p className="text-muted-foreground">
                    This is the part of the code graph the agent actually traversed: the bug report, every function, test,
                    commit and person it looked at, and the real edges between them. Click a node for details.
                  </p>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="suspects" className="mt-4 space-y-3">
          {confirmError && <p className="text-sm text-destructive">{confirmError}</p>}
          {suspects.map(s => (
            <Card key={s.id} className={s.id === verdict?.id ? 'border-red-300 dark:border-red-500/50' : ''}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-muted-foreground">#{s.rank}</span>
                      <code className="font-semibold text-sm">{s.name}</code>
                      <span className="text-xs text-muted-foreground">{s.file_path}{s.start_line ? `:${s.start_line}` : ''}</span>
                      <Badge variant="secondary" className="tabular-nums">score {s.suspicion_score.toFixed(2)}</Badge>
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">{s.reasoning}</p>
                  </div>
                  {confirmedIds.has(s.id) ? (
                    <Badge className="bg-green-600 shrink-0"><CheckCircle className="h-3 w-3 mr-1" /> Root cause</Badge>
                  ) : (
                    <Button size="sm" variant="outline" className="shrink-0" disabled={!!confirming} onClick={() => confirm(s)}>
                      {confirming === s.id ? <Loader2 className="h-3 w-3 animate-spin" /> : 'This is the root cause'}
                    </Button>
                  )}
                </div>
                <ScoreBars s={s} />
              </CardContent>
            </Card>
          ))}
          <p className="text-xs text-muted-foreground">
            Confirming writes a CONFIRMED edge from this investigation to the function. Later investigations that touch
            this function, or describe a similar bug, recall it and score it higher.
          </p>
        </TabsContent>

        <TabsContent value="fix" className="mt-4">
          {fix && (
            <div className="grid gap-4 lg:grid-cols-3">
              <div className="lg:col-span-2 space-y-4">
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base flex items-center gap-2"><Sparkles className="h-4 w-4" /> Suggested patch</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {fix.diff ? <DiffView diff={fix.diff} /> : (
                      <p className="text-sm text-muted-foreground">
                        {result?.mode === 'graph+llm'
                          ? 'No patch: the model did not find a concrete code change to make in this function.'
                          : 'No patch: this investigation ran graph-only (no LLM), so no diff was written. The root cause, its source and reviewers are below.'}
                      </p>
                    )}
                    {fix.test_suggestion && (
                      <p className="text-sm"><TestTube className="inline h-4 w-4 mr-1" /> <span className="font-medium">Regression test:</span> {fix.test_suggestion}</p>
                    )}
                    <p className="text-[11px] text-muted-foreground">Generated by a local model. Review it before applying.</p>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-base flex items-center gap-2"><FileCode className="h-4 w-4" /> Current source · {fix.target.file_path}:{fix.target.start_line}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <pre className="font-mono text-xs bg-muted rounded-lg p-4 overflow-x-auto">{fix.target.code}</pre>
                  </CardContent>
                </Card>
                {fix.history && (
                  <Card>
                    <CardHeader className="pb-2">
                      <CardTitle className="text-base flex items-center gap-2"><GitMerge className="h-4 w-4" /> How this code got here</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <CodeHistoryView history={fix.history} owners={fix.owners} />
                    </CardContent>
                  </Card>
                )}
              </div>
              <div className="space-y-4">
                {fix.owners && (
                  <Card>
                    <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><Users className="h-4 w-4" /> Who owns it · {fix.owners.file}</CardTitle></CardHeader>
                    <CardContent><OwnersView owners={fix.owners} /></CardContent>
                  </Card>
                )}
                <Card>
                  <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><Users className="h-4 w-4" /> Suggested reviewers</CardTitle></CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    {fix.reviewers.map(r => (
                      <div key={r.email}>
                        <p className="font-medium">{r.name}</p>
                        <p className="text-xs text-muted-foreground">{r.email}</p>
                        <p className="text-xs text-muted-foreground">{r.fn_commits} commits to this function · {r.file_commits} to its file</p>
                      </div>
                    ))}
                    {!fix.reviewers.length && <p className="text-muted-foreground">No commit history for this code.</p>}
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><Network className="h-4 w-4" /> Blast radius</CardTitle></CardHeader>
                  <CardContent className="text-sm space-y-2">
                    <p>{plural(fix.blast_radius.affected_functions, 'function')} in {plural(fix.blast_radius.affected_files, 'file')} call into this code.</p>
                    {fix.blast_radius.sample_callers.length > 0 && (
                      <p className="text-xs text-muted-foreground font-mono break-all">{fix.blast_radius.sample_callers.join(', ')}</p>
                    )}
                    <p className="font-medium text-xs mt-2">Tests that exercise it</p>
                    {fix.blast_radius.covering_tests.length
                      ? fix.blast_radius.covering_tests.map(t => <p key={t} className="text-xs font-mono">{t}</p>)
                      : (
                        <div className="space-y-2">
                          <p className="text-xs text-amber-800 dark:text-amber-300">None. Add a regression test.</p>
                          <Button size="sm" variant="outline" className="h-8" onClick={() => result && setTestTarget({
                            repoId: result.repository_id, functionId: fix.target.id, name: fix.target.name,
                          })}>
                            <FlaskConical className="h-3.5 w-3.5 mr-1.5" /> Suggest a test
                          </Button>
                        </div>
                      )}
                  </CardContent>
                </Card>
              </div>
            </div>
          )}
        </TabsContent>
      </Tabs>
      <SuggestTestDialog target={testTarget} onClose={() => setTestTarget(null)} />
    </div>
  )
}
