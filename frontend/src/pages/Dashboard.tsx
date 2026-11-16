import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bug,
  CheckCircle,
  Crosshair,
  FlaskConical,
  Github,
  History,
  Library,
  Loader2,
  Plus,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  useNeighborhood,
  useRepoInsights,
  useRepositories,
  useRepositoryStats,
} from "../hooks";
import {
  Graph3D,
  kindColor,
  type G3Edge,
  type G3Node,
} from "../components/three/Graph3D";
import {
  errorMessage,
  type GraphResponse,
  type Hotspot,
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
    <div>
      <p className="font-display text-2xl font-bold tabular-nums leading-none tracking-[-0.02em]">
        {value ?? 0}
      </p>
      <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
        {label}
      </p>
    </div>
  );
}

/** Short name for a node label: the last part of a qualified name, without the module path. */
const shortLabel = (label: string) =>
  /^[\w.]+$/.test(label) ? label.split(".").pop() || label : label;

/**
 * The selected repository in 3D: one function and everything one hop from it. Pick a hotspot on the right,
 * or click any function in the graph, to move the centre there.
 */
function Scope({
  graph,
  hotspots,
  focusId,
  onFocus,
}: {
  graph: GraphResponse | null;
  hotspots: Hotspot[];
  focusId: string | null;
  onFocus: (functionId: string) => void;
}) {
  const nodes = useMemo<G3Node[]>(
    () =>
      (graph?.nodes ?? []).map((n) => ({
        id: n.id,
        label: shortLabel(n.label),
        kind: n.type,
      })),
    [graph],
  );
  const edges = useMemo<G3Edge[]>(
    () =>
      (graph?.edges ?? []).map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: e.type,
      })),
    [graph],
  );
  const centre = graph?.nodes.find((n) => n.properties.id === focusId);
  const kinds = [...new Set(nodes.map((n) => n.kind))];
  const top = Math.max(...hotspots.map((h) => h.score), 0.0001);

  return (
    <div className="stage grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_19rem]">
      <div className="relative h-[340px] lg:h-[440px]">
        {nodes.length > 0 ? (
          <Graph3D
            nodes={nodes}
            edges={edges}
            focusId={centre?.id}
            labels={nodes.length > 16 ? "front" : "all"}
            onSelect={(id) => {
              const hit = graph?.nodes.find((n) => n.id === id);
              if (hit?.type === "Function" && hit.properties.id)
                onFocus(hit.properties.id);
            }}
            ariaLabel={`Graph of ${centre?.label ?? "the selected function"} and the ${nodes.length - 1} nodes one hop from it`}
          />
        ) : (
          <p className="flex h-full items-center justify-center font-mono text-xs text-white/40">
            {focusId ? "Reading the graph…" : "No call graph yet."}
          </p>
        )}
        <div className="pointer-events-none absolute left-4 top-3.5 font-mono text-[11px] text-white/50">
          <p className="uppercase tracking-[0.1em]">One hop from</p>
          <p className="mt-1 text-sm font-semibold text-white/90">
            {centre ? shortLabel(centre.label) : "…"}
          </p>
        </div>
        <ul className="pointer-events-none absolute bottom-3 left-4 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-white/50">
          {kinds.map((k) => (
            <li key={k} className="flex items-center gap-1.5">
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: kindColor(k) }}
              />
              {k}
            </li>
          ))}
        </ul>
        <p className="pointer-events-none absolute bottom-3 right-4 font-mono text-[10px] text-white/35">
          drag to orbit · click a function to move there
        </p>
      </div>

      <div className="border-t stage-rule lg:border-l lg:border-t-0">
        <p className="px-4 pb-2 pt-3.5 font-mono text-[11px] uppercase tracking-[0.1em] text-white/50">
          Hotspots · PageRank over calls
        </p>
        <ol>
          {hotspots.map((h, i) => {
            const on = h.id === focusId;
            return (
              <li key={h.id}>
                <button
                  type="button"
                  onClick={() => onFocus(h.id)}
                  aria-pressed={on}
                  className={`relative grid w-full grid-cols-[1.25rem_minmax(0,1fr)_auto] items-baseline gap-x-2 border-t stage-rule px-4 py-2.5 text-left transition-colors focus-visible:ring-offset-0 ${on ? "bg-white/[0.07]" : "hover:bg-white/[0.04]"}`}
                >
                  <span className="font-mono text-[10px] tabular-nums text-white/40">
                    {i + 1}
                  </span>
                  <span className="min-w-0">
                    <span
                      className={`block truncate font-mono text-xs ${on ? "text-signal" : "text-white/90"}`}
                    >
                      {h.name}
                    </span>
                    <span className="block truncate text-[11px] text-white/40">
                      {h.file_path}
                    </span>
                  </span>
                  <span
                    className={`font-mono text-[10px] ${h.tests ? "text-white/50" : "text-[#ffc24a]"}`}
                  >
                    {h.tests ? `${h.tests} ${h.tests === 1 ? "test" : "tests"}` : "untested"}
                  </span>
                  <span
                    aria-hidden="true"
                    className="absolute bottom-0 left-0 h-px bg-signal"
                    style={{ width: `${(h.score / top) * 100}%` }}
                  />
                </button>
              </li>
            );
          })}
        </ol>
        {!hotspots.length && (
          <p className="px-4 pb-4 text-sm text-white/50">No call graph yet.</p>
        )}
      </div>
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

  // The scope centres on the function the user picked, or the top hotspot until they pick one.
  const [picked, setPicked] = useState<{ repo: string; fn: string } | null>(
    null,
  );
  const focusId =
    (picked && picked.repo === selected?.id ? picked.fn : null) ??
    hotspots[0]?.id ??
    null;
  const neighborhood = useNeighborhood(
    selected?.status === "ready" ? selected.id : null,
    focusId,
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
          <h1 className="font-display text-4xl font-bold tracking-[-0.03em]">
            Repositories
          </h1>
          <p className="mt-1 text-muted-foreground text-sm">
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
            className={`cursor-pointer transition-colors hover:border-foreground ${selected?.id === repo.id ? "border-foreground shadow-[4px_4px_0_hsl(var(--foreground))]" : ""}`}
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
                <div className="grid grid-cols-4 gap-2 py-3 border-y">
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
                  size="sm"
                  variant="outline"
                  disabled={repo.status !== "ready"}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/brain?repo=${repo.id}`);
                  }}
                >
                  <Library className="h-4 w-4 mr-1" /> Ask
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
              <h2
                id="selected-repo"
                className="font-display text-3xl font-bold tracking-[-0.03em]"
              >
                {selected.name}
              </h2>
            </div>
            <p className="text-sm text-muted-foreground tabular-nums">
              {selected.stats.functions ?? 0} functions ·{" "}
              {selected.stats.tests ?? 0} tests ·{" "}
              {selected.stats.people ?? 0} people
            </p>
          </div>

          <Scope
            graph={neighborhood}
            hotspots={hotspots}
            focusId={focusId}
            onFocus={(fn) => setPicked({ repo: selected.id, fn })}
          />

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
