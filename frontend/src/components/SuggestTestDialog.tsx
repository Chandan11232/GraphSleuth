import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Copy, FlaskConical, Loader2, RefreshCw, AlertTriangle } from 'lucide-react'
import { api, errorMessage, type NamedRef, type TestSuggestion } from '../services/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/Dialog'
import { Button } from './ui/Button'
import { Badge } from './ui/Badge'

export interface TestTarget {
  repoId: string
  functionId: string
  name: string
}

function RefList({ title, items, empty }: { title: string; items: NamedRef[]; empty: string }) {
  return (
    <div className="space-y-1">
      <p className="eyebrow">{title}</p>
      {items.length ? (
        items.map(i => (
          <p key={`${i.file_path}:${i.name}`} className="text-xs">
            <span className="font-mono">{i.name}</span>
            <span className="text-muted-foreground"> · {i.file_path}</span>
          </p>
        ))
      ) : (
        <p className="text-xs text-muted-foreground">{empty}</p>
      )}
    </div>
  )
}

/** Asks the backend for a test: graph context for the function, plus a draft from the local model. */
export function SuggestTestDialog({ target, onClose }: { target: TestTarget | null; onClose: () => void }) {
  const [result, setResult] = useState<TestSuggestion | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [showCypher, setShowCypher] = useState(false)

  const load = useCallback(async (t: TestTarget) => {
    setLoading(true)
    setError(null)
    setResult(null)
    setCopied(false)
    try {
      const r = await api.post(`/graph/${t.repoId}/function/${t.functionId}/suggest-test`)
      setResult(r.data)
    } catch (err) {
      setError(errorMessage(err, 'Could not reach the backend to write a test.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (target) load(target)
  }, [target, load])

  const copy = async () => {
    if (!result?.code) return
    try {
      await navigator.clipboard.writeText(result.code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }

  const ctx = result?.context

  return (
    <Dialog open={!!target} onOpenChange={open => !open && onClose()}>
      <DialogContent className="max-w-4xl max-h-[88vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-primary" /> Suggested test for{' '}
            <code className="font-mono text-base">{target?.name}</code>
          </DialogTitle>
          <DialogDescription>
            The graph supplies the function, how its callers use it, what it calls and the closest existing tests.
            The local model writes a draft from that.
          </DialogDescription>
        </DialogHeader>

        {loading && (
          <div className="flex items-center gap-3 rounded-md border border-dashed p-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin text-primary" />
            Reading {target?.name} and its neighbours from the graph, then writing a test. This takes about 20 seconds.
          </div>
        )}

        {error && <p className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}

        {result && ctx && (
          <div className="grid gap-5 md:grid-cols-[1fr_15rem]">
            <div className="min-w-0 space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <code className="font-mono text-sm font-medium">{result.test_path}</code>
                <Badge variant="secondary">{result.framework}</Badge>
                {result.model && <Badge variant="outline">draft by {result.model}</Badge>}
              </div>

              {result.error && (
                <div className="flex gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
                  <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                  <span>{result.error}</span>
                </div>
              )}

              {result.code && (
                <div className="relative">
                  <pre className="max-h-[48vh] overflow-auto rounded-md bg-sidebar p-4 font-mono text-xs leading-relaxed text-sidebar-foreground">
                    {result.code}
                  </pre>
                  <Button size="sm" variant="secondary" className="absolute right-2 top-2 h-7" onClick={copy}>
                    {copied ? <Check className="h-3.5 w-3.5 mr-1" /> : <Copy className="h-3.5 w-3.5 mr-1" />}
                    {copied ? 'Copied' : 'Copy'}
                  </Button>
                </div>
              )}
              {result.covers && (
                <p className="text-sm"><span className="font-medium">Covers:</span> {result.covers}</p>
              )}
              {result.code && (
                <p className="text-xs text-muted-foreground">
                  A small local model wrote this draft. Run it and check each assertion before you commit it.
                </p>
              )}
              <div className="flex gap-2">
                <Button size="sm" variant="outline" onClick={() => target && load(target)} disabled={loading}>
                  <RefreshCw className="h-3.5 w-3.5 mr-1.5" /> Write another draft
                </Button>
              </div>
            </div>

            <aside className="space-y-4 rounded-md bg-muted/60 p-4">
              <p className="text-xs text-muted-foreground">{ctx.summary}</p>
              <RefList title={`Called by (${ctx.caller_count})`} items={ctx.callers}
                       empty="Nothing in the repo calls it. It is likely an entry point, such as an API handler." />
              <RefList title="Calls" items={ctx.callees} empty="It calls no other repo functions." />
              <RefList
                title={ctx.examples_are_nearby ? 'Nearby tests used as examples' : 'Example tests (from elsewhere)'}
                items={ctx.example_tests}
                empty="The repo has no tests in this language yet."
              />
              <button
                onClick={() => setShowCypher(!showCypher)}
                className="flex items-center gap-1 text-xs font-medium text-primary"
              >
                {showCypher ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />} Cypher used
              </button>
              {showCypher && (
                <pre className="overflow-x-auto rounded bg-sidebar p-2 font-mono text-[10px] text-sidebar-foreground">
                  {ctx.cypher.join('\n\n')}
                </pre>
              )}
            </aside>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
