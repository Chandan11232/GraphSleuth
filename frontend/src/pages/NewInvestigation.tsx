import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Loader2, Search, Wand2 } from 'lucide-react'
import { useRepositories } from '../hooks'
import { api, errorMessage } from '../services/api'
import { Button } from '../components/ui/Button'
import { Card, CardContent, CardHeader, CardTitle } from '../components/ui/Card'
import { Input } from '../components/ui/Input'
import { Label } from '../components/ui/Label'

const DEMO_BUG = {
  title: 'Refund of unknown order returns 500',
  description: `Refunding an order that does not exist crashes the refund endpoint with a 500 instead of returning 404.

Traceback (most recent call last):
  File "shop/api/handlers.py", line 14, in handle_refund_request
    result = process_refund(request["order_id"], float(request["amount"]))
  File "shop/payments/refunds.py", line 17, in process_refund
    validate_refund_amount(order, amount)
  File "shop/payments/refunds.py", line 9, in validate_refund_amount
    remaining = order["total"] - order["refunded"]
TypeError: 'NoneType' object is not subscriptable`,
}

export function NewInvestigation() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const { repositories, loading } = useRepositories()
  const ready = repositories.filter(r => r.status === 'ready')
  const [form, setForm] = useState({ repository_id: params.get('repo') || '', title: '', description: '', use_llm: true })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!form.repository_id && ready.length) setForm(f => ({ ...f, repository_id: ready[0].id }))
  }, [ready, form.repository_id])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setError(null)
    try {
      const r = await api.post('/investigations', form)
      navigate(`/investigations/${r.data.id}`)
    } catch (err) {
      setError(errorMessage(err, 'Could not start the investigation'))
      setSubmitting(false)
    }
  }

  return (
    <div className="p-6 max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">New investigation</h1>
        <p className="text-muted-foreground text-sm">
          Paste a bug report. Stack traces, function names and error messages all help the agent find entry points in the graph.
        </p>
      </div>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">Bug report</CardTitle>
          <Button type="button" variant="outline" size="sm" onClick={() => setForm(f => ({ ...f, ...DEMO_BUG }))}>
            <Wand2 className="h-4 w-4 mr-1" /> Fill demo bug
          </Button>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="repo">Repository</Label>
              <select
                id="repo"
                value={form.repository_id}
                onChange={e => setForm({ ...form, repository_id: e.target.value })}
                className="w-full h-10 rounded-lg border border-input bg-background px-3 text-sm"
                disabled={loading}
                required
              >
                <option value="">{ready.length ? 'Select a repository…' : 'No ready repositories'}</option>
                {ready.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="title">Title</Label>
              <Input id="title" value={form.title} onChange={e => setForm({ ...form, title: e.target.value })}
                     placeholder="Refund of unknown order returns 500" required minLength={3} />
            </div>
            <div className="space-y-2">
              <Label htmlFor="desc">Description</Label>
              <textarea
                id="desc"
                value={form.description}
                onChange={e => setForm({ ...form, description: e.target.value })}
                className="w-full min-h-[220px] rounded-lg border border-input bg-background px-3 py-2 text-sm font-mono"
                placeholder="What happens, what you expected, and any stack trace…"
                required
                minLength={10}
              />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={form.use_llm} onChange={e => setForm({ ...form, use_llm: e.target.checked })} />
              Let the LLM drive extra graph tools and write a patch (needs Ollama; graph-only otherwise)
            </label>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" disabled={submitting || !form.repository_id}>
              {submitting ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Search className="h-4 w-4 mr-2" />}
              Start investigation
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
