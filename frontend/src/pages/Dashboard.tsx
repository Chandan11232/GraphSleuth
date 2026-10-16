import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Bug,
  CheckCircle,
  Flame,
  Github,
  History,
  Loader2,
  Plus,
  ShieldAlert,
  Trash2,
  XCircle,
} from "lucide-react";
import { useRepoInsights, useRepositories, useRepositoryStats } from "../hooks";
import { errorMessage, type Repository } from "../services/api";
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
        <Badge className="bg-green-100 text-green-800">
          <CheckCircle className="h-3 w-3 mr-1" /> Ready
        </Badge>
      );
    case "error":
      return (
        <Badge className="bg-red-100 text-red-800">
          <XCircle className="h-3 w-3 mr-1" /> Error
        </Badge>
      );
    default:
      return (
        <Badge className="bg-blue-100 text-blue-800">
          <Loader2 className="h-3 w-3 mr-1 animate-spin" />{" "}
          {repo.progress || repo.status}
        </Badge>
      );
  }
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
    <div className="p-6 space-y-6 max-w-7xl">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Repositories</h1>
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
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">
                Graph contents · {selected.name}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm space-y-3">
              {stats ? (
                <>
                  <div>
                    <p className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
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
                    <p className="text-xs uppercase tracking-wide text-muted-foreground mb-1">
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
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2">
                <Flame className="h-4 w-4 text-orange-500" /> Hotspots
                (PageRank)
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {hotspots.map((h) => (
                <div
                  key={h.id}
                  className="flex items-center justify-between text-sm gap-2"
                >
                  <div className="min-w-0">
                    <p className="font-mono text-xs truncate">{h.name}</p>
                    <p className="text-[11px] text-muted-foreground truncate">
                      {h.file_path}
                    </p>
                  </div>
                  <Badge
                    variant={h.tests ? "secondary" : "outline"}
                    className={h.tests ? "" : "text-amber-700 border-amber-300"}
                  >
                    {h.tests ? `${h.tests} tests` : "untested"}
                  </Badge>
                </div>
              ))}
              {!hotspots.length && (
                <p className="text-sm text-muted-foreground">
                  No call graph yet.
                </p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base flex items-center gap-2">
                  <ShieldAlert className="h-4 w-4 text-amber-600" /> Risky
                  recent changes
                </CardTitle>
                <select
                  value={commitLimit}
                  onChange={(e) => setCommitLimit(Number(e.target.value))}
                  className="text-xs bg-background border rounded px-1.5 py-0.5 text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value={3}>3</option>
                  <option value={5}>5</option>
                  <option value={7}>7</option>
                  <option value={10}>10</option>
                </select>
              </div>
              <p className="text-xs text-muted-foreground">
                Untested functions changed in the last {commitLimit} commits
              </p>
            </CardHeader>
            <CardContent className="space-y-2">
              {riskyChanges.map((r) => (
                <div key={r.id} className="text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-mono text-xs truncate">{r.function}</p>
                    <span className="text-[11px] text-muted-foreground shrink-0">
                      {r.changed_by.join(", ")}
                    </span>
                  </div>
                  <p
                    className="text-[11px] text-muted-foreground truncate"
                    title={r.commits.join("\n")}
                  >
                    {r.file} · “{r.commits[0]}”
                  </p>
                </div>
              ))}
              {!riskyChanges.length && (
                <p className="text-sm text-muted-foreground">
                  No untested recent changes.
                </p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base flex items-center gap-2">
                <History className="h-4 w-4" /> Agent memory
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {investigations.map((inv) => (
                <button
                  key={inv.id}
                  onClick={() => navigate(`/investigations/${inv.id}`)}
                  className="w-full text-left p-2 rounded-md hover:bg-muted/60 border border-border"
                >
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium truncate">{inv.title}</p>
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
                    <p className="text-[11px] text-green-700 mt-0.5">
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
        </div>
      )}

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
