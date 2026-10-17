import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bug,
  CheckCircle,
  Crosshair,
  Flame,
  FlaskConical,
  Github,
  History,
  Loader2,
  Plus,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";
import { useRepoInsights, useRepositories, useRepositoryStats } from "../hooks";
import {
  errorMessage,
  type Repository,
  type RiskyChange,
} from "../services/api";
import {
  SuggestTestDialog,
  type TestTarget,
} from "../components/SuggestTestDialog";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/Dialog";
import { Button } from "../components/ui/Button";
import { Input } from "../components/ui/Input";
import { Label } from "../components/ui/Label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "../components/ui/Card";
import { Badge } from "../components/ui/Badge";

function StatusBadge({ repo }: { repo: Repository }) {
  switch (repo.status) {
    case "ready":
      return (
        <Badge className="bg-green-100 text-green-800 dark:bg-green-500/15 dark:text-green-300">
          <CheckCircle className="h-3 w-3 mr-1" /> Ready
        </Badge>
      );
    case "error":
      return (
        <Badge className="bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-300">
          <XCircle className="h-3 w-3 mr-1" /> Error
        </Badge>
      );
    default:
      return (
        <Badge className="bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-300">
          <Loader2 className="h-3 w-3 mr-1 animate-spin" />{" "}
          {repo.progress || repo.status}
        </Badge>
      );
  }
}

type RiskLevel = "high" | "medium" | "low";

const RISK_BAR: Record<RiskLevel, string> = {
  high: "bg-risk-high",
  medium: "bg-risk-medium",
  low: "bg-risk-low",
};

const RISK_LABEL: Record<RiskLevel, string> = {
  high: "2+ callers",
  medium: "1 caller",
  low: "entry point",
};

const riskLevel = (callers: number): RiskLevel =>
  callers >= 2 ? "high" : callers === 1 ? "medium" : "low";

const shortDate = (ms: number) =>
  new Date(ms).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

/** A bug report the agent can start from: names the function so the graph search finds it. */
function investigatePrefill(r: RiskyChange) {
  const callers =
    r.callers === 1 ? "1 function calls it" : `${r.callers} functions call it`;
  return {
    title: `Review recent change to ${r.function}`,
    description:
      `${r.function} in ${r.file} changed recently and no test covers it.\n` +
      `Changed by ${r.changed_by.join(", ")}: "${r.commits[0]}".\n` +
      `${callers}, so a defect here would reach them too.\n\n` +
      `Check ${r.function} for a defect introduced by this change. ` +
      `Add any symptom, error message or stack trace you have seen.`,
  };
}

function RiskyRow({
  change: r,
  onInvestigate,
  onSuggestTest,
}: {
  change: RiskyChange;
  onInvestigate: () => void;
  onSuggestTest: () => void;
}) {
  const level = riskLevel(r.callers);
  return (
    <li className="flex gap-3 py-3 first:pt-0 last:pb-0">
      <span
        className={`w-1 shrink-0 rounded-full ${RISK_BAR[level]}`}
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-64">
          <div className="flex items-center gap-2 flex-wrap">
            <code className="font-mono text-sm font-medium break-all">
              {r.function}
            </code>
            <span className="text-xs text-muted-foreground tabular-nums">
              {r.callers} {r.callers === 1 ? "caller" : "callers"}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 truncate">
            {r.file} · {r.changed_by.join(", ")} ·{" "}
            <span className="tabular-nums">{shortDate(r.last_changed)}</span>
          </p>
          <p
            className="text-xs text-muted-foreground truncate"
            title={r.commits.join("\n")}
          >
            “{r.commits[0]}”
            {r.commits.length > 1 && ` and ${r.commits.length - 1} more`}
          </p>
        </div>
        <div className="flex gap-1.5 shrink-0">
          <Button size="sm" variant="outline" className="h-8" onClick={onInvestigate}>
            <Crosshair className="h-3.5 w-3.5 mr-1.5" /> Investigate
          </Button>
          <Button size="sm" variant="outline" className="h-8" onClick={onSuggestTest}>
            <FlaskConical className="h-3.5 w-3.5 mr-1.5" /> Suggest test
          </Button>
        </div>
      </div>
    </li>
  );
}

function Stat({ value, label }: { value: number | undefined; label: string }) {
  return (
    <div className="text-center">
      <p className="text-xl font-bold tabular-nums">{value ?? 0}</p>
      <p className="text-[11px] text-muted-foreground">{label}</p>
    </div>
  );
}

export function Dashboard() {
  const navigate = useNavigate();
  const { repositories, loading, error, createRepository, deleteRepository } =
    useRepositories();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    name: "",
    url: "",
    branch: "",
    github_token: "",
  });
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [commitLimit, setCommitLimit] = useState<number>(5);
  const [testTarget, setTestTarget] = useState<TestTarget | null>(null);

  const selected =
    repositories.find((r) => r.id === selectedId) ||
    repositories.find((r) => r.status === "ready") ||
    null;
  const stats = useRepositoryStats(
    selected?.id ?? null,
    selected?.status === "ready",
  );
  const { hotspots, riskyChanges, investigations } = useRepoInsights(
    selected?.id ?? null,
    selected?.status === "ready",
    commitLimit,
  );

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    setFormError(null);
    try {
      const repo = await createRepository({
        name: form.name || form.url.split("/").filter(Boolean).pop() || "repo",
        url: form.url.trim(),
        branch: form.branch || undefined,
        github_token: form.github_token || undefined,
      });
      setSelectedId(repo.id);
      setShowCreate(false);
      setForm({ name: "", url: "", branch: "", github_token: "" });
    } catch (err) {
      setFormError(errorMessage(err, "Failed to connect repository"));
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (pendingDelete !== id) {
      setPendingDelete(id);
      return;
    }
    setPendingDelete(null);
    await deleteRepository(id);
    if (selectedId === id) setSelectedId(null);
  };

  return (
    <div className="px-6 py-6 lg:px-8 space-y-8 max-w-7xl">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold">Repositories</h1>
          <p className="text-muted-foreground text-sm">
            Each repository becomes its own FalkorDB graph of functions, calls,
            tests, commits and people.
          </p>
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus className="h-4 w-4 mr-2" /> Connect repository
        </Button>
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 border border-destructive/20 rounded-lg text-destructive text-sm">
          {error}
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {repositories.map((repo) => (
          <Card
            key={repo.id}
            onClick={() => setSelectedId(repo.id)}
            className={`cursor-pointer transition-shadow hover:shadow-md ${selected?.id === repo.id ? "ring-2 ring-primary" : ""}`}
          >
            <CardHeader className="pb-3">
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-3 min-w-0">
                  <Github className="h-5 w-5 text-muted-foreground shrink-0" />
                  <div className="min-w-0">
                    <CardTitle className="text-base truncate">
                      {repo.name}
                    </CardTitle>
                    <p className="text-xs text-muted-foreground truncate">
                      {repo.url}
                    </p>
                  </div>
                </div>
                <StatusBadge repo={repo} />
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {repo.status === "ready" && (
                <div className="grid grid-cols-4 gap-2 py-2 border-y">
                  <Stat value={repo.stats.functions} label="functions" />
                  <Stat value={repo.stats.calls} label="calls" />
                  <Stat value={repo.stats.tests} label="tests" />
                  <Stat value={repo.stats.commits} label="commits" />
                </div>
              )}
              {repo.status === "error" && (
                <p className="text-xs text-destructive">{repo.error_message}</p>
              )}
              <div className="flex items-center justify-end gap-2">
                <Button
                  size="sm"
                  disabled={repo.status !== "ready"}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/investigate?repo=${repo.id}`);
                  }}
                >
                  <Bug className="h-4 w-4 mr-1" /> Investigate a bug
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(repo.id);
                  }}
                  className="text-destructive hover:text-destructive"
                >
                  {pendingDelete === repo.id ? (
                    "Confirm delete"
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                </Button>
              </div>
            </CardContent>
          </Card>
        ))}

        {repositories.length === 0 && !loading && (
          <Card className="col-span-full border-dashed border-2">
            <CardContent className="py-12 text-center space-y-3">
              <Github className="h-10 w-10 mx-auto text-muted-foreground/50" />
              <h3 className="text-lg font-medium">No repositories yet</h3>
              <p className="text-muted-foreground text-sm max-w-md mx-auto">
                Connect a public GitHub repository, or run{" "}
                <code className="px-1 bg-muted rounded">
                  python demo/create_demo_repo.py
                </code>{" "}
                and connect the server path it prints to try the built-in demo.
              </p>
              <Button onClick={() => setShowCreate(true)}>
                <Plus className="h-4 w-4 mr-2" /> Connect repository
              </Button>
            </CardContent>
          </Card>
        )}
      </div>

      {selected?.status === "ready" && (
        <section className="space-y-4" aria-labelledby="selected-repo">
          <div className="flex items-end justify-between gap-4 flex-wrap">
            <div>
              <p className="eyebrow">Selected repository</p>
              <h2 id="selected-repo" className="text-xl font-semibold">
                {selected.name}
              </h2>
            </div>
            <p className="text-sm text-muted-foreground tabular-nums">
              {selected.stats.functions ?? 0} functions ·{" "}
              {selected.stats.tests ?? 0} tests ·{" "}
              {selected.stats.people ?? 0} people
            </p>
          </div>

          <div className="grid gap-4 lg:grid-cols-3 items-start">
            <Card className="lg:col-span-2">
              <CardHeader className="pb-3 space-y-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <ShieldAlert className="h-4 w-4 text-risk-medium" /> Risky
                  recent changes
                </CardTitle>
                <p className="text-sm text-muted-foreground flex items-center gap-1.5 flex-wrap">
                  Untested functions changed in the last
                  <select
                    id="commit-window"
                    aria-label="Commits to look back"
                    value={commitLimit}
                    onChange={(e) => setCommitLimit(Number(e.target.value))}
                    className="text-sm font-medium bg-background border border-input rounded-md px-1.5 py-0.5 text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <option value={3}>3</option>
                    <option value={5}>5</option>
                    <option value={7}>7</option>
                    <option value={10}>10</option>
                  </select>
                  commits, most-called first.
                </p>
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  {(["high", "medium", "low"] as const).map((level) => (
                    <span key={level} className="flex items-center gap-1.5">
                      <span
                        className={`h-2.5 w-1 rounded-full ${RISK_BAR[level]}`}
                      />
                      {RISK_LABEL[level]}
                    </span>
                  ))}
                </div>
              </CardHeader>
              <CardContent>
                {riskyChanges.length ? (
                  <ul className="divide-y">
                    {riskyChanges.map((r) => (
                      <RiskyRow
                        key={r.id}
                        change={r}
                        onInvestigate={() =>
                          navigate(`/investigate?repo=${selected.id}`, {
                            state: { prefill: investigatePrefill(r) },
                          })
                        }
                        onSuggestTest={() =>
                          setTestTarget({
                            repoId: selected.id,
                            functionId: r.id,
                            name: r.function,
                          })
                        }
                      />
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-muted-foreground py-6 text-center">
                    No untested changes in the last {commitLimit} commits.
                  </p>
                )}
              </CardContent>
            </Card>

            <div className="space-y-4">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <Flame className="h-4 w-4 text-risk-high" /> Hotspots
                    <span className="font-normal text-muted-foreground">
                      PageRank over calls
                    </span>
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <ol className="space-y-2">
                    {hotspots.map((h, i) => (
                      <li
                        key={h.id}
                        className="flex items-center justify-between text-sm gap-2"
                      >
                        <span className="text-xs text-muted-foreground tabular-nums w-4">
                          {i + 1}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className="font-mono text-xs truncate">{h.name}</p>
                          <p className="text-[11px] text-muted-foreground truncate">
                            {h.file_path}
                          </p>
                        </div>
                        <Badge
                          variant={h.tests ? "secondary" : "outline"}
                          className={
                            h.tests ? "" : "text-amber-800 border-amber-300 dark:text-amber-300 dark:border-amber-500/40"
                          }
                        >
                          {h.tests ? `${h.tests} tests` : "untested"}
                        </Badge>
                      </li>
                    ))}
                  </ol>
                  {!hotspots.length && (
                    <p className="text-sm text-muted-foreground">
                      No call graph yet.
                    </p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm flex items-center gap-2">
                    <History className="h-4 w-4 text-primary" /> Agent memory
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {investigations.map((inv) => (
                    <button
                      key={inv.id}
                      onClick={() => navigate(`/investigations/${inv.id}`)}
                      className="w-full text-left p-2 rounded-md hover:bg-accent border border-border transition-colors"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm font-medium truncate">
                          {inv.title}
                        </p>
                        <Badge
                          variant={
                            inv.status === "confirmed" ? "default" : "secondary"
                          }
                          className="shrink-0"
                        >
                          {inv.status}
                        </Badge>
                      </div>
                      {inv.confirmed.length > 0 && (
                        <p className="text-[11px] text-primary mt-0.5">
                          root cause: {inv.confirmed.join(", ")}
                        </p>
                      )}
                    </button>
                  ))}
                  {!investigations.length && (
                    <p className="text-sm text-muted-foreground">
                      No investigations yet. They are stored in the graph and
                      recalled by later ones.
                    </p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Graph contents</CardTitle>
                </CardHeader>
                <CardContent className="text-sm space-y-3">
                  {stats ? (
                    <>
                      <div>
                        <p className="eyebrow mb-1.5">
                          Nodes ({stats.total_nodes})
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                          {Object.entries(stats.nodes).map(([k, v]) => (
                            <Badge key={k} variant="secondary">
                              {k} {v}
                            </Badge>
                          ))}
                        </div>
                      </div>
                      <div>
                        <p className="eyebrow mb-1.5">
                          Edges ({stats.total_edges})
                        </p>
                        <div className="flex flex-wrap gap-1.5">
                          {Object.entries(stats.edges).map(([k, v]) => (
                            <Badge key={k} variant="outline">
                              {k} {v}
                            </Badge>
                          ))}
                        </div>
                      </div>
                    </>
                  ) : (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </section>
      )}

      <SuggestTestDialog
        target={testTarget}
        onClose={() => setTestTarget(null)}
      />

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Connect a repository</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="url">Git URL or server path</Label>
              <Input
                id="url"
                value={form.url}
                onChange={(e) => setForm({ ...form, url: e.target.value })}
                placeholder="https://github.com/owner/repo"
                required
              />
              <p className="text-xs text-muted-foreground">
                Python, JavaScript, TypeScript, Go and Java are parsed. The last
                300 commits are read for history.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="from URL"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="branch">Branch</Label>
                <Input
                  id="branch"
                  value={form.branch}
                  onChange={(e) => setForm({ ...form, branch: e.target.value })}
                  placeholder="default"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="token">GitHub token (private repos only)</Label>
              <Input
                id="token"
                type="password"
                value={form.github_token}
                onChange={(e) =>
                  setForm({ ...form, github_token: e.target.value })
                }
                placeholder="optional"
              />
            </div>
            {formError && (
              <p className="text-sm text-destructive">{formError}</p>
            )}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setShowCreate(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={creating || !form.url}>
                {creating ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "Connect"
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
