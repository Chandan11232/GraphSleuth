import { AlertTriangle, CircleDot, GitMerge, GitPullRequest, Landmark, Users } from 'lucide-react'
import type { CodeHistory as History, Decision, Ownership } from '../../services/api'
import { Badge } from '../ui/Badge'

function day(ms: number | null | undefined) {
  return ms ? new Date(ms).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : ''
}

function ExtLink({ url, children }: { url?: string | null; children: React.ReactNode }) {
  return url ? (
    <a href={url} target="_blank" rel="noreferrer" className="hover:underline">{children}</a>
  ) : <>{children}</>
}

export function DecisionItem({ d, showCode = true }: { d: Decision; showCode?: boolean }) {
  const later = d.drift[0]
  return (
    <div className={`rounded-md border p-3 text-sm space-y-1.5 ${d.drift.length ? 'border-amber-300 bg-amber-50 dark:border-amber-500/40 dark:bg-amber-500/10' : ''}`}>
      <p className="flex gap-2">
        <Landmark className="h-4 w-4 shrink-0 mt-0.5 text-yellow-600" />
        <span>“{d.text}”</span>
      </p>
      <p className="text-xs text-muted-foreground pl-6">
        {d.author ?? 'Someone'} in{' '}
        <ExtLink url={d.url}>#{d.item_number} {d.item_title}</ExtLink>
        {d.at_ms ? ` · ${day(d.at_ms)}` : ''}
      </p>
      {showCode && d.functions.length > 0 && (
        <p className="text-xs pl-6 font-mono text-muted-foreground break-all">
          governs {d.functions.map(f => f.name).join(', ')}
        </p>
      )}
      {later && (
        <p className="text-xs pl-6 flex gap-1.5 text-amber-800 dark:text-amber-300">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-px" />
          <span>
            Possibly outdated: {later.functions.join(', ')} changed since in{' '}
            {later.pr ? `PR #${later.pr} ` : ''}“{later.message}” by {later.author}
            {later.reviewers.length ? `, approved by ${later.reviewers.join(', ')}` : ''}
            {d.drift.length > 1 ? ` (+${d.drift.length - 1} more)` : ''}.
          </span>
        </p>
      )}
    </div>
  )
}

/** How a function got to its current state: pull requests, reviews, fixed issues, decisions, open issues. */
export function CodeHistoryView({ history, owners }: { history: History; owners?: Ownership | null }) {
  const team = new Set([...(owners?.teams ?? []).flatMap(t => t.members), ...(owners?.owners ?? []).map(o => o.name)])
  const withPr = history.changes.filter(c => c.pull_request)
  const bare = history.changes.filter(c => !c.pull_request)
  const open = history.discussions.filter(d => d.state === 'open')
  if (!history.changes.length && !history.decisions.length && !history.discussions.length) {
    return <p className="text-sm text-muted-foreground">No commits, pull requests or discussions on record for this code.</p>
  }
  return (
    <div className="space-y-4">
      {open.length > 0 && (
        <div className="space-y-1">
          {open.map(d => (
            <p key={d.id} className="text-sm flex gap-2">
              <CircleDot className="h-4 w-4 shrink-0 mt-0.5 text-green-600" />
              <span>Open {d.type === 'Issue' ? 'issue' : 'PR'} <ExtLink url={d.url}>#{d.number} {d.title}</ExtLink> mentions it</span>
            </p>
          ))}
        </div>
      )}
      {history.decisions.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Decisions about this code</p>
          {history.decisions.map(d => <DecisionItem key={d.id} d={d} showCode={false} />)}
        </div>
      )}
      {withPr.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Pull requests that changed it, newest first</p>
          {withPr.map(c => {
            const pr = c.pull_request!
            const approvers = pr.reviews.filter(r => r.state === 'APPROVED').map(r => r.name)
            const author = pr.opened_by ?? c.author
            const outsideOwners = team.size > 0 && approvers.length > 0 && !approvers.some(a => team.has(a) && a !== author)
            return (
              <div key={c.sha} className="text-sm space-y-0.5">
                <p className="flex gap-2">
                  <GitMerge className="h-4 w-4 shrink-0 mt-0.5 text-cyan-600" />
                  <ExtLink url={pr.url}><span className="font-medium">#{pr.number}</span> {pr.title}</ExtLink>
                </p>
                <p className="text-xs text-muted-foreground pl-6">
                  {author} · {day(pr.merged_ms ?? c.timestamp)} ·{' '}
                  {approvers.length ? `approved by ${approvers.join(', ')}` : 'no approving review'}
                  {pr.comments ? ` · ${pr.comments} comment${pr.comments === 1 ? '' : 's'}` : ''}
                </p>
                {outsideOwners && (
                  <p className="text-xs pl-6 text-amber-800 dark:text-amber-300">
                    No approver from {owners!.teams.map(t => '@' + t.team).join(', ') || 'the code owners'}
                  </p>
                )}
                {pr.fixes.map(i => (
                  <p key={i.id} className="text-xs text-muted-foreground pl-6">
                    fixed <ExtLink url={i.url}>#{i.number} {i.title}</ExtLink>{i.opened_by ? ` (reported by ${i.opened_by})` : ''}
                  </p>
                ))}
              </div>
            )
          })}
        </div>
      )}
      {bare.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Commits with no pull request on record</p>
          {bare.slice(0, 4).map(c => (
            <p key={c.sha} className="text-xs flex gap-2">
              <GitPullRequest className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span><code>{c.sha.slice(0, 7)}</code> {c.message} · {c.author}</span>
            </p>
          ))}
        </div>
      )}
    </div>
  )
}

export function OwnersView({ owners }: { owners: Ownership }) {
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap gap-1.5">
        {owners.teams.map(t => (
          <Badge key={t.team} variant="outline" title={`CODEOWNERS ${t.pattern}`}>
            <Users className="h-3 w-3 mr-1" /> @{t.team}
          </Badge>
        ))}
        {owners.owners.map(o => <Badge key={o.name} variant="outline" title={`CODEOWNERS ${o.pattern}`}>{o.name}</Badge>)}
        {!owners.teams.length && !owners.owners.length && (
          <span className="text-xs text-muted-foreground">No CODEOWNERS entry for {owners.file}</span>
        )}
      </div>
      {owners.ask.map(p => (
        <div key={p.name}>
          <p className="font-medium">{p.name}</p>
          <p className="text-xs text-muted-foreground">{p.reasons.join(' · ')}</p>
        </div>
      ))}
    </div>
  )
}
