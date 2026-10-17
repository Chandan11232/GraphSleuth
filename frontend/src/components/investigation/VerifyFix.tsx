import { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle, Check, CheckCircle, ChevronDown, ChevronRight, Copy, Crosshair, Download, ExternalLink, FlaskConical,
  GitPullRequest, Loader2, Lock, Play, ShieldAlert, ShieldCheck, Users, XCircle,
} from 'lucide-react'
import {
  API_BASE, api, errorMessage, postEvents,
  type AnalysisStep, type FixDraft, type Outcome, type Verification,
} from '../../services/api'
import { Button } from '../ui/Button'
import { Badge } from '../ui/Badge'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/Card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/Dialog'
import { DiffView, StepCard } from './Trace'

const STATUS: Record<Verification['status'], { label: string; tone: string; Icon: any }> = {
  verified: { label: 'Fix verified', tone: 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-500/40 dark:bg-emerald-500/10 dark:text-emerald-200', Icon: ShieldCheck },
  regresses: { label: 'Fix breaks other tests', tone: 'border-red-300 bg-red-50 text-red-900 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-200', Icon: XCircle },
  not_fixed: { label: 'Bug still reproduces', tone: 'border-red-300 bg-red-50 text-red-900 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-200', Icon: XCircle },
  not_reproduced: { label: 'Test does not reproduce the bug', tone: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200', Icon: ShieldAlert },
  no_regressions: { label: 'No regressions, but unproven', tone: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200', Icon: ShieldAlert },
  error: { label: 'Tests could not run', tone: 'border-red-300 bg-red-50 text-red-900 dark:border-red-500/40 dark:bg-red-500/10 dark:text-red-200', Icon: AlertTriangle },
}

function OutcomeBadge({ o }: { o: Outcome }) {
  const cls = o === 'passed'
    ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300'
    : o === 'skipped' || o === 'missing'
      ? 'bg-muted text-muted-foreground'
      : 'bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300'
  return <span className={`px-1.5 py-0.5 rounded text-[11px] font-medium ${cls}`}>{o === 'missing' ? 'not run' : o}</span>
}

function regressionTemplate(d: FixDraft): string {
  const module = d.function.file_path.replace(/\.py$/, '').replace(/\/__init__$/, '').replace(/^src\//, '').replace(/\//g, '.')
  return `import pytest

from ${module} import ${d.function.name.split('.').pop()}


def test_${d.function.name.split('.').pop()}_regression():
    # Call the code the way the bug report describes, and assert the correct behaviour.
    # This test should fail on the current code and pass once the fix is in.
    ...
`
}

function Editor({ label, hint, value, onChange, rows }: {
  label: string; hint?: string; value: string; onChange: (v: string) => void; rows: number
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-medium">{label}</p>
        {hint && <p className="text-[11px] text-muted-foreground truncate">{hint}</p>}
      </div>
      <textarea
        value={value}
        onChange={e => onChange(e.target.value)}
        rows={rows}
        spellCheck={false}
        className="w-full font-mono text-xs bg-muted rounded-lg p-3 border border-transparent focus:border-primary focus:outline-none resize-y"
      />
    </div>
  )
}

/** Closes the loop: prove the fix in a sandbox, then (with a person's approval) open the pull request. */
export function VerifyFix({ investigationId, onVerified }: { investigationId: string; onVerified: () => void }) {
  const [draft, setDraft] = useState<FixDraft | null>(null)
  const [draftError, setDraftError] = useState<string | null>(null)
  const [patched, setPatched] = useState('')
  const [test, setTest] = useState('')
  const [steps, setSteps] = useState<AnalysisStep[]>([])
  const [message, setMessage] = useState('')
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [result, setResult] = useState<Verification | null>(null)
  const [history, setHistory] = useState<Verification[]>([])
  const [showCypher, setShowCypher] = useState(false)

  const loadHistory = useCallback(async () => {
    try {
      const r = await api.get(`/investigations/${investigationId}/fixes`)
      setHistory(r.data)
      return r.data as Verification[]
    } catch {
      return []
    }
  }, [investigationId])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const past = await loadHistory()
      if (cancelled) return
      if (past.length) setResult(past[0])
      try {
        const r = await api.post(`/investigations/${investigationId}/fix/draft`)
        if (cancelled) return
        const d: FixDraft = r.data
        setDraft(d)
        setPatched(past[0]?.patched_code || d.patched_code || d.original_code)
        setTest(past[0]?.test_code || d.test_code || regressionTemplate(d))
      } catch (err) {
        if (!cancelled) setDraftError(errorMessage(err, 'Could not prepare the fix for verification.'))
      }
    })()
    return () => { cancelled = true }
  }, [investigationId, loadHistory])

  const run = async () => {
    if (!draft) return
    setRunning(true)
    setRunError(null)
    setSteps([])
    setResult(null)
    try {
      await postEvents(`/investigations/${investigationId}/verify`,
        { patched_code: patched, test_code: test, test_path: draft.test_path },
        ev => {
          if (ev.type === 'step') setSteps(prev => [...prev, ev.step])
          else if (ev.type === 'status') setMessage(ev.message)
          else if (ev.type === 'error') setRunError(ev.message)
          else if (ev.type === 'result') setResult(ev.result)
        })
      const past = await loadHistory()
      if (past[0]?.status === 'verified') onVerified()
    } catch (err: any) {
      setRunError(err?.message || 'Verification failed')
    } finally {
      setRunning(false)
    }
  }

  if (draftError) {
    return <Card><CardContent className="pt-6 text-sm text-red-700 dark:text-red-300">{draftError}</CardContent></Card>
  }
  if (!draft) {
    return (
      <Card><CardContent className="pt-6 flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Selecting tests from the graph and drafting a regression test…
      </CardContent></Card>
    )
  }

  const unchanged = patched.trim() === draft.original_code.trim()
  const disabled = running || !!draft.blocked || !draft.supported || unchanged

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="lg:col-span-2 space-y-4">
        {result && <ResultCard result={result} investigationId={investigationId} draft={draft} onChange={loadHistory} />}

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2"><FlaskConical className="h-4 w-4" /> Prove the fix in a sandbox</CardTitle>
            <p className="text-xs text-muted-foreground">
              The regression test runs on the original code (it should fail), then with the fix (it should pass), together
              with every test the graph says can reach {draft.function.name}. Edit either before running.
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {draft.blocked && (
              <p className="text-sm flex gap-2 text-amber-900 dark:text-amber-200"><Lock className="h-4 w-4 shrink-0 mt-0.5" /> {draft.blocked}</p>
            )}
            {!draft.supported && <p className="text-sm text-muted-foreground">{draft.note}</p>}
            <Editor label={`Corrected ${draft.function.name}`} rows={Math.min(22, patched.split('\n').length + 2)}
              hint={draft.patched_code ? 'written by the model during the investigation' : 'no patch was written: edit the current source'}
              value={patched} onChange={setPatched} />
            <Editor label={`Regression test · ${draft.test_path}`} rows={Math.min(22, test.split('\n').length + 2)}
              hint={draft.test_model ? `drafted by ${draft.test_model}` : draft.note || undefined}
              value={test} onChange={setTest} />
            <div className="flex items-center gap-3 flex-wrap">
              <Button onClick={run} disabled={disabled}>
                {running ? <Loader2 className="h-4 w-4 mr-1.5 animate-spin" /> : <Play className="h-4 w-4 mr-1.5" />}
                {running ? message || 'Running…' : result ? 'Run again' : 'Run in sandbox'}
              </Button>
              {unchanged && !draft.blocked && <p className="text-xs text-muted-foreground">Change the function to verify a fix.</p>}
            </div>
            {runError && <p className="text-sm text-red-700 dark:text-red-300">{runError}</p>}
          </CardContent>
        </Card>

        {(steps.length > 0 || result?.steps?.length) && (
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base">Verification trace</CardTitle></CardHeader>
            <CardContent>
              {(steps.length ? steps : result?.steps || []).map(s => <StepCard key={s.step} step={s} />)}
              {running && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> {message}</div>}
            </CardContent>
          </Card>
        )}
      </div>

      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2"><Crosshair className="h-4 w-4" /> Tests selected by the graph</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p><span className="text-2xl font-semibold tabular-nums">{draft.impacted_tests.length}</span>
              <span className="text-muted-foreground"> of {draft.total_tests} tests can reach this code</span></p>
            {draft.impacted_tests.map(t => (
              <p key={t.id} className="text-xs"><span className="font-mono">{t.name}</span>
                <span className="text-muted-foreground"> · {t.distance === 0 ? 'tests it directly' : `${t.distance} call${t.distance > 1 ? 's' : ''} away`}</span></p>
            ))}
            {!draft.impacted_tests.length && <p className="text-xs text-amber-800 dark:text-amber-300">No existing test reaches it, so the regression test is the only proof.</p>}
            <button onClick={() => setShowCypher(!showCypher)} className="text-xs text-primary flex items-center gap-1">
              {showCypher ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Cypher
            </button>
            {showCypher && <pre className="p-3 bg-sidebar text-sidebar-foreground rounded-md font-mono text-[11px] overflow-x-auto">{draft.cypher}</pre>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm flex items-center gap-2"><Lock className="h-4 w-4" /> Sandbox</CardTitle></CardHeader>
          <CardContent className="text-xs text-muted-foreground space-y-1">
            <p>A throwaway copy of the repository. Tests run in a child process with no server credentials in its
              environment, its own HOME, CPU and file-size limits, and a timeout.</p>
            <p>Results are saved to the graph as <span className="font-mono">Fix</span> and <span className="font-mono">TestRun</span> nodes.
              A verified fix confirms the root cause in agent memory.</p>
          </CardContent>
        </Card>
        {history.length > 1 && (
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-sm">Earlier attempts</CardTitle></CardHeader>
            <CardContent className="space-y-2">
              {history.map(h => (
                <button key={h.id} onClick={() => setResult(h)} className="block w-full text-left text-xs hover:bg-muted rounded p-1.5">
                  <span className="font-medium">{STATUS[h.status].label}</span>
                  <span className="text-muted-foreground"> · {new Date(h.created_at).toLocaleString()}</span>
                  <p className="text-muted-foreground truncate">{h.headline}</p>
                </button>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}

function ResultCard({ result, investigationId, draft, onChange }: {
  result: Verification; investigationId: string; draft: FixDraft; onChange: () => void
}) {
  const s = STATUS[result.status]
  const [copied, setCopied] = useState(false)
  const [open, setOpen] = useState(false)
  const [showBody, setShowBody] = useState(false)
  const pr = result.pull_request

  const copy = async () => {
    await navigator.clipboard.writeText(`${result.pr_draft.title}\n\n${result.pr_draft.body}`)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <Card className={`border ${s.tone}`}>
      <CardContent className="pt-5 space-y-4">
        <div className="flex items-start gap-3">
          <s.Icon className="h-6 w-6 shrink-0" />
          <div className="min-w-0">
            <p className="font-semibold">{s.label}</p>
            <p className="text-sm">{result.headline}</p>
            <p className="text-xs opacity-80 mt-0.5">{result.explanation}</p>
          </div>
        </div>
        <div className="rounded-lg border bg-background text-foreground overflow-x-auto">
          <table className="w-full text-xs">
            <thead><tr className="text-left text-muted-foreground border-b">
              <th className="p-2 font-medium">Test</th><th className="p-2 font-medium">Original code</th><th className="p-2 font-medium">With the fix</th>
            </tr></thead>
            <tbody>
              {result.comparison.map(r => (
                <tr key={`${r.file_path}:${r.name}`} className="border-b last:border-0 align-top">
                  <td className="p-2">
                    <span className="font-mono">{r.name}</span>
                    {r.regression && <Badge variant="outline" className="ml-1.5 text-[10px]">regression test</Badge>}
                    {(r.after_message || r.before_message) && (
                      <p className="text-[11px] text-muted-foreground mt-0.5 font-mono break-all">{(r.after !== 'passed' ? r.after_message : r.before_message).split('\n')[0].slice(0, 160)}</p>
                    )}
                  </td>
                  <td className="p-2"><OutcomeBadge o={r.before} /></td>
                  <td className="p-2"><OutcomeBadge o={r.after} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {result.diff && <DiffView diff={result.diff} />}

        <div className="rounded-lg border bg-background text-foreground p-3 space-y-2">
          <div className="flex items-center gap-2 flex-wrap">
            <GitPullRequest className="h-4 w-4" />
            <p className="text-sm font-medium flex-1 min-w-0 truncate">{result.pr_draft.title}</p>
            <span className="text-[11px] font-mono text-muted-foreground">{result.pr_draft.branch}</span>
          </div>
          {result.pr_draft.reviewers.length > 0 && (
            <p className="text-xs text-muted-foreground flex items-center gap-1.5 flex-wrap"><Users className="h-3.5 w-3.5" /> Reviewers:
              {result.pr_draft.reviewers.map(r => <span key={r.name} className="text-foreground">{r.login ? `@${r.login}` : r.name}</span>)}
              {result.pr_draft.fixes_issues.map(n => <Badge key={n} variant="outline" className="text-[10px]">Fixes #{n}</Badge>)}
            </p>
          )}
          <button onClick={() => setShowBody(!showBody)} className="text-xs text-primary flex items-center gap-1">
            {showBody ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Description written from the graph
          </button>
          {showBody && <pre className="text-xs whitespace-pre-wrap bg-muted rounded p-3 max-h-96 overflow-y-auto">{result.pr_draft.body}</pre>}
          <div className="flex gap-2 flex-wrap pt-1">
            {pr ? (
              <a href={pr.url} target="_blank" rel="noreferrer">
                <Button size="sm"><ExternalLink className="h-3.5 w-3.5 mr-1.5" /> Pull request #{pr.number}</Button>
              </a>
            ) : (
              <Button size="sm" onClick={() => setOpen(true)} disabled={!draft.can_open_pr}
                title={draft.can_open_pr ? '' : 'Only for repositories connected from a github.com URL'}>
                <GitPullRequest className="h-3.5 w-3.5 mr-1.5" /> Open pull request…
              </Button>
            )}
            <a href={`${API_BASE}/investigations/${investigationId}/fixes/${result.id}/patch`}>
              <Button size="sm" variant="outline"><Download className="h-3.5 w-3.5 mr-1.5" /> Download .patch</Button>
            </a>
            <Button size="sm" variant="outline" onClick={copy}>
              {copied ? <Check className="h-3.5 w-3.5 mr-1.5" /> : <Copy className="h-3.5 w-3.5 mr-1.5" />} Copy description
            </Button>
          </div>
          {!draft.can_open_pr && <p className="text-[11px] text-muted-foreground">This repository was connected from a directory, so there is no GitHub repository to open the pull request on. The patch applies with <span className="font-mono">git am</span>.</p>}
        </div>
      </CardContent>
      <ApproveDialog open={open} onClose={() => setOpen(false)} result={result} investigationId={investigationId}
        needsToken={!draft.server_token} onDone={onChange} />
    </Card>
  )
}

function ApproveDialog({ open, onClose, result, investigationId, needsToken, onDone }: {
  open: boolean; onClose: () => void; result: Verification; investigationId: string; needsToken: boolean; onDone: () => void
}) {
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const verified = result.status === 'verified'

  const approve = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.post(`/investigations/${investigationId}/fixes/${result.id}/pull-request`,
        { approve: true, github_token: token || undefined })
      onDone()
      onClose()
    } catch (err) {
      setError(errorMessage(err, 'Could not open the pull request'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={o => !o && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Open this pull request?</DialogTitle>
          <DialogDescription>
            The agent never opens a pull request on its own. This creates branch <span className="font-mono">{result.pr_draft.branch}</span>,
            commits {Object.keys(result.files || {}).length || 1} file(s) and opens the pull request
            {verified ? '' : ' as a draft, because the fix is not verified'}.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <p className="flex items-center gap-2">
            {verified ? <CheckCircle className="h-4 w-4 text-emerald-600" /> : <ShieldAlert className="h-4 w-4 text-amber-600" />}
            {result.headline}
          </p>
          {needsToken && (
            <input type="password" value={token} onChange={e => setToken(e.target.value)} placeholder="GitHub token with write access"
              className="w-full rounded-md border bg-background px-3 py-2 text-sm" />
          )}
          {error && <p className="text-red-700 dark:text-red-300">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>Cancel</Button>
            <Button onClick={approve} disabled={busy || (needsToken && !token)}>
              {busy && <Loader2 className="h-4 w-4 mr-1.5 animate-spin" />} Approve and open
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
