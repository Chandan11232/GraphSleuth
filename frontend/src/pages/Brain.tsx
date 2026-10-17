import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, BookOpen, ChevronDown, ChevronRight, Landmark, Library, Loader2, MessageSquare, Network, RefreshCw,
  Search, User, Users,
} from 'lucide-react'
import { useRepositories } from '../hooks'
import { api, errorMessage, type BrainAnswer, type BrainOverview, type BrainSource, type GraphNode } from '../services/api'
import { Button } from '../components/ui/Button'
import { Badge } from '../components/ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card'
import { Input } from '../components/ui/Input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui/Tabs'
import { EvidenceGraph } from '../components/investigation/EvidenceGraph'
import { DecisionItem } from '../components/knowledge/CodeHistory'

const SOURCE_LABEL: Record<BrainSource['type'], string> = {
  Issue: 'Issue', PullRequest: 'PR', Comment: 'Comment', Decision: 'Decision', Team: 'Team',
}

/** Renders `code` spans and [n] citations, which jump to their source. */
function AnswerText({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  return (
    <div className="space-y-2">
      {text.split('\n').filter(Boolean).map((line, i) => <AnswerLine key={i} text={line} onCite={onCite} />)}
    </div>
  )
}

function AnswerLine({ text, onCite }: { text: string; onCite: (n: number) => void }) {
  const parts = text.split(/(\[\d+\]|`[^`]+`)/g)
  return (
    <p className="text-[15px] leading-7">
      {parts.map((p, i) => {
        const cite = p.match(/^\[(\d+)\]$/)
        if (cite) {
          return (
            <button key={i} type="button" onClick={() => onCite(Number(cite[1]))}
                    className="mx-0.5 align-super text-[10px] font-semibold rounded bg-primary/10 text-primary px-1 hover:bg-primary/20">
              {cite[1]}
            </button>
          )
        }
        if (p.startsWith('`') && p.endsWith('`')) return <code key={i} className="px-1 bg-muted rounded text-[13px]">{p.slice(1, -1)}</code>
        return <span key={i}>{p}</span>
      })}
    </p>
  )
}

function SourceRow({ s, highlight }: { s: BrainSource; highlight: boolean }) {
  return (
    <div id={`source-${s.n}`} className={`flex gap-3 rounded-md border p-3 text-sm transition-colors ${highlight ? 'border-primary bg-accent' : ''}`}>
      <span className="text-xs font-semibold text-primary tabular-nums w-5 shrink-0">{s.n}</span>
      <div className="min-w-0 space-y-1">
        <p className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline">{SOURCE_LABEL[s.type]}{s.number ? ` #${s.number}` : ''}</Badge>
          {s.url ? <a href={s.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">{s.title}</a>
            : <span className="font-medium">{s.title}</span>}
          {s.author && <span className="text-xs text-muted-foreground">by {s.author}</span>}
        </p>
        {s.text && <p className="text-xs text-muted-foreground line-clamp-3">“{s.text}”</p>}
        <p className="text-[11px] text-muted-foreground">found by: {s.why}</p>
      </div>
    </div>
  )
}

function StepRow({ step }: { step: BrainAnswer['steps'][number] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-md border">
      <button type="button" onClick={() => setOpen(o => !o)} className="w-full flex items-start gap-2 p-3 text-left text-sm">
        {open ? <ChevronDown className="h-4 w-4 mt-0.5 shrink-0" /> : <ChevronRight className="h-4 w-4 mt-0.5 shrink-0" />}
        <span><code className="font-semibold">{step.tool}</code> <span className="text-muted-foreground">{step.summary}</span></span>
      </button>
      {open && <pre className="font-mono text-xs bg-muted rounded-b-md p-3 overflow-x-auto whitespace-pre">{step.query}</pre>}
    </div>
  )
}

function examples(overview: BrainOverview | null): string[] {
  if (!overview) return []
  const out: string[] = []
  const drifted = overview.decisions.find(d => d.drift.length)
  const fn = (drifted?.drift[0]?.functions[0] ?? overview.decisions[0]?.functions[0]?.name)?.split('.').pop()
  if (fn) out.push(`Why is ${fn} the way it is?`, `Who should review a change to ${fn}?`)
  const other = overview.decisions.find(d => d !== drifted) ?? drifted
  if (other) out.push(`What did we decide about ${other.item_title.toLowerCase()}?`)
  if (overview.knowledge_gaps[0]) out.push(`Who knows ${overview.knowledge_gaps[0].path}?`)
  return out.slice(0, 4)
}

export function Brain() {
  const [params, setParams] = useSearchParams()
  const { repositories, loading } = useRepositories()
  const ready = repositories.filter(r => r.status === 'ready')
  const repoId = params.get('repo') || ready[0]?.id || ''
  const [overview, setOverview] = useState<BrainOverview | null>(null)
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<BrainAnswer | null>(null)
  const [asking, setAsking] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cited, setCited] = useState<number | null>(null)
  const [tab, setTab] = useState('sources')
  const [selected, setSelected] = useState<GraphNode | null>(null)

  const loadOverview = (id: string) =>
    api.get<BrainOverview>(`/brain/${id}/overview`).then(r => setOverview(r.data)).catch(() => setOverview(null))

  useEffect(() => {
    setAnswer(null)
    setOverview(null)
    if (repoId) loadOverview(repoId)
  }, [repoId])

  // /brain?repo=…&q=… asks straight away, so a question can be shared as a link.
  const linked = params.get('q')
  useEffect(() => {
    if (repoId && linked) ask(linked)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, linked])

  const ask = async (q: string) => {
    if (!repoId || q.trim().length < 3) return
    setQuestion(q)
    setAsking(true)
    setError(null)
    setCited(null)
    try {
      const r = await api.post<BrainAnswer>(`/brain/${repoId}/ask`, { question: q, use_llm: true })
      setAnswer(r.data)
      setTab('sources')
      setSelected(null)
    } catch (err) {
      setError(errorMessage(err, 'Could not answer the question'))
    } finally {
      setAsking(false)
    }
  }

  const sync = async () => {
    setSyncing(true)
    setError(null)
    try {
      await api.post(`/brain/${repoId}/sync`)
      await loadOverview(repoId)
    } catch (err) {
      setError(errorMessage(err, 'Sync failed'))
    } finally {
      setSyncing(false)
    }
  }

  const cite = (n: number) => {
    setTab('sources')
    setCited(n)
    setTimeout(() => document.getElementById(`source-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50)
  }

  const suggestions = useMemo(() => examples(overview), [overview])
  const status = overview?.status
  const drifted = overview?.decisions.filter(d => d.drift.length) ?? []
  // One entry per owner, listing every CODEOWNERS pattern that names them.
  const owners = useMemo(() => {
    const by = new Map<string, { kind: string; owner: string; patterns: string[]; files: number; members: string[] }>()
    for (const o of overview?.ownership ?? []) {
      const key = `${o.kind}:${o.owner}`
      const cur = by.get(key) ?? { kind: o.kind, owner: o.owner, patterns: [], files: 0, members: o.members }
      cur.patterns.push(o.pattern)
      cur.files += o.files
      by.set(key, cur)
    }
    return [...by.values()].sort((a, b) => Number(b.kind === 'Team') - Number(a.kind === 'Team') || b.files - a.files)
  }, [overview])

  return (
    <div className="px-6 py-6 lg:px-8 space-y-6 max-w-7xl">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2"><Library className="h-6 w-6" /> Company brain</h1>
          <p className="text-muted-foreground text-sm max-w-2xl">
            Issues, pull requests, reviews, decisions and CODEOWNERS, linked to the code graph. Ask why code is the way
            it is or who to talk to. Every answer cites the record behind it.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={repoId}
            onChange={e => setParams({ repo: e.target.value })}
            className="h-9 rounded-lg border border-input bg-background px-3 text-sm"
            disabled={loading}
            aria-label="Repository"
          >
            {!ready.length && <option value="">No ready repositories</option>}
            {ready.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
          <Button variant="outline" size="sm" onClick={sync} disabled={!repoId || syncing} title="Fetch what changed in the tracker since the last sync">
            {syncing ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <RefreshCw className="h-4 w-4 mr-1" />} Sync
          </Button>
        </div>
      </div>

      {status && (
        <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
          <span>Source: <span className="text-foreground">{status.source === 'github' ? 'GitHub API' : status.source === 'file' ? 'tracker export (.cd-knowledge.json)' : 'none found'}</span></span>
          <span><span className="text-foreground tabular-nums">{status.issues}</span> issues</span>
          <span><span className="text-foreground tabular-nums">{status.pull_requests}</span> pull requests</span>
          <span><span className="text-foreground tabular-nums">{status.decisions}</span> decisions</span>
          <span><span className="text-foreground tabular-nums">{status.teams}</span> teams</span>
          {status.synced_at && <span>synced {new Date(status.synced_at).toLocaleString()}</span>}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="lg:col-span-2 space-y-4 min-w-0">
          <Card>
            <CardContent className="pt-5 space-y-3">
              <form onSubmit={e => { e.preventDefault(); ask(question) }} className="flex gap-2">
                <Input value={question} onChange={e => setQuestion(e.target.value)} aria-label="Question"
                       placeholder="Why does process_refund use find_order?" className="flex-1" />
                <Button type="submit" disabled={asking || !repoId || question.trim().length < 3}>
                  {asking ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Search className="h-4 w-4 mr-2" />} Ask
                </Button>
              </form>
              {suggestions.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {suggestions.map(s => (
                    <button key={s} type="button" onClick={() => ask(s)} disabled={asking}
                            className="text-xs rounded-full border px-3 py-1 hover:bg-accent text-left">
                      {s}
                    </button>
                  ))}
                </div>
              )}
              {error && <p className="text-sm text-destructive">{error}</p>}
            </CardContent>
          </Card>

          {answer && (
            <Card>
              <CardHeader className="pb-2 flex flex-row items-center justify-between space-y-0 gap-2">
                <CardTitle className="text-base flex items-center gap-2"><BookOpen className="h-4 w-4" /> Answer</CardTitle>
                <Badge variant="secondary">built from the graph</Badge>
              </CardHeader>
              <CardContent className="space-y-4">
                {answer.summary && (
                  <div className="rounded-md border border-dashed p-3 space-y-1">
                    <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                      Summary by {answer.model} · check it against the cited facts below
                    </p>
                    <AnswerText text={answer.summary} onCite={cite} />
                  </div>
                )}
                <AnswerText text={answer.answer} onCite={cite} />
                <Tabs value={tab} onValueChange={setTab}>
                  <TabsList>
                    <TabsTrigger value="sources"><MessageSquare className="h-4 w-4 mr-1.5" /> Sources ({answer.sources.length})</TabsTrigger>
                    <TabsTrigger value="graph" disabled={!answer.graph.nodes.length}><Network className="h-4 w-4 mr-1.5" /> Graph</TabsTrigger>
                    <TabsTrigger value="cypher"><Search className="h-4 w-4 mr-1.5" /> Queries ({answer.steps.length})</TabsTrigger>
                  </TabsList>
                  <TabsContent value="sources" className="mt-3 space-y-2">
                    {answer.sources.map(s => <SourceRow key={s.key} s={s} highlight={cited === s.n} />)}
                    {!answer.sources.length && <p className="text-sm text-muted-foreground">No sources.</p>}
                  </TabsContent>
                  <TabsContent value="graph" className="mt-3">
                    <div className="h-[460px] rounded-md border overflow-hidden">
                      <EvidenceGraph graph={answer.graph} onSelect={setSelected} initialHidden={['Comment']}
                                     hint="the nodes and edges this answer was built from · click a type to hide it" />
                    </div>
                    {selected && (
                      <p className="text-xs text-muted-foreground mt-2 break-all">
                        <Badge variant="outline" className="mr-2">{selected.type}</Badge>{selected.label}
                        {selected.properties.url && <> · <a className="hover:underline" href={selected.properties.url} target="_blank" rel="noreferrer">open</a></>}
                      </p>
                    )}
                  </TabsContent>
                  <TabsContent value="cypher" className="mt-3 space-y-2">
                    {answer.steps.map((s, i) => <StepRow key={i} step={s} />)}
                  </TabsContent>
                </Tabs>
              </CardContent>
            </Card>
          )}

          {overview && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2"><Landmark className="h-4 w-4" /> Decisions</CardTitle>
                <p className="text-xs text-muted-foreground">{overview.summaries.decisions}. A decision is flagged when the code it governs changed after it was made, outside the pull request that made it.</p>
              </CardHeader>
              <CardContent className="space-y-2">
                {[...drifted, ...overview.decisions.filter(d => !d.drift.length)].slice(0, 12).map(d => <DecisionItem key={d.id} d={d} />)}
                {!overview.decisions.length && (
                  <p className="text-sm text-muted-foreground">No decisions found in the discussions yet.</p>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        <div className="space-y-4 min-w-0">
          {drifted.length > 0 && (
            <div className="flex gap-3 rounded-md border border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10 p-3 text-sm">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5 text-amber-600" />
              <p>{drifted.length} decision{drifted.length === 1 ? '' : 's'} may be out of date: the code changed after the decision was made.</p>
            </div>
          )}
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><Users className="h-4 w-4" /> Who owns what</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              {owners.map(o => (
                <div key={`${o.kind}-${o.owner}`}>
                  <p className="flex items-center justify-between gap-2">
                    <span className="font-medium flex items-center gap-1">
                      {o.kind === 'Team' ? `@${o.owner}` : <><User className="h-3.5 w-3.5" /> {o.owner}</>}
                    </span>
                    <span className="text-xs text-muted-foreground tabular-nums">{o.files} file{o.files === 1 ? '' : 's'}</span>
                  </p>
                  <p className="text-xs font-mono text-muted-foreground break-all">{o.patterns.join('  ')}</p>
                  {o.members.length > 0 && <p className="text-xs text-muted-foreground">{o.members.join(', ')}</p>}
                </div>
              ))}
              {overview && !overview.ownership.length && <p className="text-muted-foreground text-xs">No CODEOWNERS file in this repository.</p>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2"><User className="h-4 w-4" /> Known by one person</CardTitle>
              <p className="text-xs text-muted-foreground">Files only one person has ever changed, most-called first.</p>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {overview?.knowledge_gaps.map(g => (
                <button key={g.path} type="button" onClick={() => ask(`Who knows ${g.path}?`)}
                        className="w-full text-left rounded-md hover:bg-accent px-2 py-1 -mx-2">
                  <p className="font-mono text-xs break-all">{g.path}</p>
                  <p className="text-xs text-muted-foreground">
                    only {g.only_author} · {g.callers} caller{g.callers === 1 ? '' : 's'}
                    {g.owners === 0 && <span className="text-amber-700 dark:text-amber-300"> · no code owner</span>}
                  </p>
                </button>
              ))}
              {overview && !overview.knowledge_gaps.length && <p className="text-muted-foreground text-xs">Every file has more than one author.</p>}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
