"""Builds the code graph for one repository.

Pipeline: clone -> parse every source file -> resolve call sites and imports
-> read git history -> write everything to FalkorDB in UNWIND batches.
"""
import os
import re
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

import structlog

from app.core.config import settings
from app.core.falkordb import get_falkordb
from app.models.graph_models import INDEX_QUERIES
from app.services import cypher_queries as Q
from app.services.knowledge import ingest_knowledge
from app.services.parsing import (
    SELF_RECEIVERS,
    FunctionDef,
    ParsedFile,
    body_tokens_for,
    language_for,
    parse_file,
    tokens_for,
)

logger = structlog.get_logger()

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", "venv", ".venv", "env", "dist", "build", "target",
    "vendor", "third_party", ".next", ".nuxt", "coverage", "site-packages", ".tox", ".mypy_cache",
}
MAX_FILE_BYTES = 400_000
BATCH = 500

# Method names so common that, without a same-class or imported match, a call
# like `obj.get()` is far more likely to hit a library than a repo function.
COMMON_METHODS = {
    "get", "set", "put", "post", "delete", "update", "append", "extend", "pop", "push", "map", "filter",
    "reduce", "join", "split", "format", "keys", "values", "items", "copy", "read", "write", "close",
    "open", "send", "add", "remove", "clear", "find", "replace", "strip", "lower", "upper", "toString",
    "then", "catch", "log", "error", "info", "warn", "debug", "emit", "on", "call", "apply", "bind",
    "run", "start", "stop", "init", "load", "save", "next", "parse", "execute", "query", "fetch",
    "len", "print", "str", "int", "list", "dict", "isinstance", "super", "range", "sorted", "min", "max",
}

ProgressFn = Callable[[str, Dict[str, int]], None]


@dataclass
class CommitInfo:
    sha: str
    author: str
    email: str
    timestamp_ms: int
    message: str
    files: Dict[str, Tuple[int, int, List[Tuple[int, int]]]]  # path -> (added, removed, new-side ranges)


class CodeGraphBuilder:
    def __init__(self, graph_name: str, repo_id: str, repo_name: str = "", repo_url: str = "",
                 token: Optional[str] = None):
        self.graph_name = graph_name
        self.repo_id = repo_id
        self.repo_name = repo_name
        self.repo_url = repo_url
        self.token = token
        self.db = get_falkordb()

    # ------------------------------------------------------------ top level

    def ingest(self, repo_path: str, progress: Optional[ProgressFn] = None) -> Dict[str, int]:
        notify = progress or (lambda stage, stats: None)
        stats: Dict[str, int] = defaultdict(int)

        notify("parsing", stats)
        files = self._parse_repository(repo_path, stats)
        functions = [f for pf in files for f in pf.functions if not f.is_test]
        tests = [f for pf in files for f in pf.functions if f.is_test]
        stats["functions"] = len(functions)
        stats["tests"] = len(tests)
        stats["classes"] = sum(len(pf.classes) for pf in files)

        notify("resolving", stats)
        resolver = CallResolver(files)
        calls = resolver.resolve_calls(functions)
        test_edges = resolver.resolve_calls(tests, tests_only=True)
        imports = resolver.file_imports()
        stats["calls"] = len(calls)
        stats["test_links"] = len(test_edges)
        stats["imports"] = len(imports)

        notify("history", stats)
        commits = read_git_history(repo_path, settings.COMMIT_HISTORY_LIMIT)
        stats["commits"] = len(commits)

        notify("writing", stats)
        self.db.delete_graph(self.graph_name)
        self._ensure_indexes()
        self._write_structure(files, functions, tests)
        self._write_edges(Q.WRITE_CALLS, calls)
        self._write_edges(Q.WRITE_TESTS, test_edges)
        self._write_edges(Q.WRITE_IMPORTS, imports)
        touched = self._write_history(commits, files)
        stats["commit_function_links"] = touched
        stats["people"] = len({c.email for c in commits})

        notify("knowledge", stats)
        knowledge = ingest_knowledge(self.graph_name, repo_path, self.repo_url, self.token,
                                     read_merge_commits(repo_path))
        stats.update({f"knowledge_{k}" if k in ("issues", "comments", "reviews", "teams") else k: v
                      for k, v in knowledge.items()})
        return dict(stats)

    # -------------------------------------------------------------- parsing

    def _parse_repository(self, repo_path: str, stats: Dict[str, int]) -> List[ParsedFile]:
        parsed: List[ParsedFile] = []
        languages: Dict[str, int] = defaultdict(int)
        for root, dirs, names in os.walk(repo_path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(names):
                full = os.path.join(root, name)
                rel = PurePosixPath(Path(os.path.relpath(full, repo_path)).as_posix()).as_posix()
                lang = language_for(rel)
                if not lang or name.endswith((".min.js", ".d.ts")):
                    continue
                try:
                    if os.path.getsize(full) > MAX_FILE_BYTES:
                        continue
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except OSError:
                    continue
                if not content.strip():
                    continue
                try:
                    pf = parse_file(rel, content)
                except Exception as e:  # one broken file must not stop ingestion
                    logger.warning("Parse failed", file=rel, error=str(e))
                    continue
                parsed.append(pf)
                languages[pf.language] += 1
                if len(parsed) >= settings.MAX_FILES_PER_REPO:
                    break
        stats["files"] = len(parsed)
        for lang, n in languages.items():
            stats[f"lang_{lang}"] = n
        return parsed

    # -------------------------------------------------------------- writing

    def _ensure_indexes(self) -> None:
        for q in INDEX_QUERIES:
            try:
                self.db.query(self.graph_name, q)
            except Exception as e:
                logger.warning("Index creation skipped", query=q, error=str(e))

    def _batched(self, cypher: str, rows: List[dict], **extra) -> None:
        for i in range(0, len(rows), BATCH):
            self.db.query(self.graph_name, cypher, {"rows": rows[i:i + BATCH], **extra})

    def _write_structure(self, files: List[ParsedFile], functions: List[FunctionDef], tests: List[FunctionDef]) -> None:
        self.db.query(self.graph_name, Q.WRITE_REPOSITORY, {
            "id": self.repo_id, "name": self.repo_name, "url": self.repo_url,
        })
        self._batched(Q.WRITE_FILES, [
            {"path": pf.path, "language": pf.language, "loc": pf.loc, "is_test": any(f.is_test for f in pf.functions),
             "module_code": pf.module_code}
            for pf in files
        ], repo_id=self.repo_id)
        self._batched(Q.WRITE_CLASSES, [
            {"id": c.id, "name": c.name, "file_path": c.file_path, "start_line": c.start_line, "end_line": c.end_line}
            for pf in files for c in pf.classes
        ])
        test_files = {pf.path for pf in files if any(f.is_test for f in pf.functions)}
        self._batched(Q.WRITE_FUNCTIONS, [
            {
                "id": f.id, "name": f.name, "qualified_name": f.qualified_name, "file_path": f.file_path,
                "start_line": f.start_line, "end_line": f.end_line, "signature": f.signature,
                "doc": f.doc, "code": f.code, "complexity": f.complexity,
                "loc": f.end_line - f.start_line + 1, "tokens": tokens_for(f), "body_tokens": body_tokens_for(f),
                "class_name": f.class_name or "", "in_test_file": f.file_path in test_files,
            }
            for f in functions
        ])
        class_ids = {(c.file_path, c.name): c.id for pf in files for c in pf.classes}
        self._batched(Q.WRITE_HAS_METHOD, [
            {"class_id": class_ids[(f.file_path, f.class_name)], "fn_id": f.id}
            for f in functions if f.class_name and (f.file_path, f.class_name) in class_ids
        ])
        self._batched(Q.WRITE_TEST_NODES, [
            {"id": t.id, "name": t.name, "file_path": t.file_path, "start_line": t.start_line,
             "end_line": t.end_line, "code": t.code}
            for t in tests
        ])

    def _write_edges(self, cypher: str, rows: List[dict]) -> None:
        self._batched(cypher, rows)

    def _write_history(self, commits: List[CommitInfo], files: List[ParsedFile]) -> int:
        if not commits:
            return 0
        known = {pf.path for pf in files}
        by_file: Dict[str, List[FunctionDef]] = defaultdict(list)
        for pf in files:
            by_file[pf.path].extend(f for f in pf.functions if not f.is_test)

        self._batched(Q.WRITE_COMMITS, [
            {"sha": c.sha, "message": c.message[:300], "timestamp": c.timestamp_ms,
             "author": c.author, "email": c.email}
            for c in commits
        ])
        modifies, touched = [], []
        for c in commits:
            for path, (added, removed, ranges) in c.files.items():
                if path not in known:
                    continue
                modifies.append({"sha": c.sha, "path": path, "added": added, "removed": removed})
                # Line ranges refer to the file as of that commit; for recent
                # commits they line up closely with today's function spans.
                for fn in by_file.get(path, []):
                    if any(start <= fn.end_line and end >= fn.start_line for start, end in ranges):
                        touched.append({"sha": c.sha, "fn_id": fn.id})
        self._batched(Q.WRITE_MODIFIES, modifies)
        self._batched(Q.WRITE_TOUCHED, touched)
        return len(touched)


class CallResolver:
    """Maps call sites (a name plus optional receiver) to function ids."""

    def __init__(self, files: List[ParsedFile]):
        self.files = files
        self.by_name: Dict[str, List[FunctionDef]] = defaultdict(list)
        self.class_names: Set[str] = set()
        self.fn_file: Dict[str, str] = {}
        for pf in files:
            for c in pf.classes:
                self.class_names.add(c.name)
            for f in pf.functions:
                if f.is_test:
                    continue
                self.by_name[f.name].append(f)
                self.fn_file[f.id] = f.file_path
        self.paths = {pf.path for pf in files}
        self.imports: Dict[str, Set[str]] = {pf.path: self._resolve_imports(pf) for pf in files}

    def _resolve_imports(self, pf: ParsedFile) -> Set[str]:
        out: Set[str] = set()
        base = PurePosixPath(pf.path).parent
        for imp in pf.imports:
            candidates: List[str] = []
            if pf.language == "python":
                dotted = imp.lstrip(".")
                levels = len(imp) - len(dotted)
                rel = dotted.replace(".", "/")
                anchor = base
                for _ in range(max(levels - 1, 0)):
                    anchor = anchor.parent
                roots = [anchor] if levels else [PurePosixPath("")] + [PurePosixPath(p) for p in ("src", "lib")]
                for root in roots:
                    stem = (root / rel).as_posix() if rel else root.as_posix()
                    candidates += [f"{stem}.py", f"{stem}/__init__.py"]
            elif imp.startswith("."):
                stem = os.path.normpath((base / imp).as_posix())
                candidates += [stem] + [f"{stem}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs")]
                candidates += [f"{stem}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")]
            elif pf.language == "java":
                candidates += [p for p in self.paths if p.endswith(imp.replace(".", "/") + ".java")]
            elif pf.language == "go":
                tail = imp.split("/")[-1]
                candidates += [p for p in self.paths if PurePosixPath(p).parent.name == tail]
            out.update(c for c in candidates if c in self.paths and c != pf.path)
        return out

    def file_imports(self) -> List[dict]:
        return [{"src": src, "dst": dst} for src, dsts in self.imports.items() for dst in dsts]

    def resolve_calls(self, callers: Iterable[FunctionDef], tests_only: bool = False) -> List[dict]:
        edges: Dict[Tuple[str, str], dict] = {}
        for caller in callers:
            for site in caller.calls:
                for target, how in self._resolve(caller, site.name, site.receiver):
                    if target.id == caller.id:
                        continue
                    key = (caller.id, target.id)
                    if key in edges:
                        edges[key]["count"] += 1
                        continue
                    edges[key] = {"src": caller.id, "dst": target.id, "line": site.line,
                                  "resolution": how, "count": 1}
            for cb in caller.registers:
                edges.setdefault((caller.id, cb.id), {"src": caller.id, "dst": cb.id, "line": cb.start_line,
                                                      "resolution": "callback", "count": 1})
        return list(edges.values())

    def _resolve(self, caller: FunctionDef, name: str, receiver: Optional[str]) -> List[Tuple[FunctionDef, str]]:
        cands = self.by_name.get(name, [])
        if receiver == "__new__":
            cands = [c for c in self.by_name.get("__init__", []) + self.by_name.get("constructor", [])
                     if c.class_name == name]
            return [(c, "constructor") for c in cands[:1]]
        if not cands:
            return []
        recv = (receiver or "").split(".")[0].split("(")[0]

        if recv in SELF_RECEIVERS and caller.class_name:
            same = [c for c in cands if c.class_name == caller.class_name and c.file_path == caller.file_path]
            if same:
                return [(same[0], "self")]
        if recv and recv in self.class_names:
            same = [c for c in cands if c.class_name == recv]
            if same:
                return [(same[0], "class")]
        if not receiver:
            same_file = [c for c in cands if c.file_path == caller.file_path and not c.class_name]
            if same_file:
                return [(same_file[0], "same_file")]
        imported = self.imports.get(caller.file_path, set())
        via_import = [c for c in cands if c.file_path in imported]
        if via_import:
            return [(via_import[0], "imported")] if len(via_import) == 1 else [(c, "imported") for c in via_import[:2]]
        if name in COMMON_METHODS or name.startswith("__"):
            return []
        if len(cands) == 1:
            return [(cands[0], "unique_name")]
        if len(cands) == 2:
            return [(c, "ambiguous") for c in cands]
        return []


# ------------------------------------------------------------------ git history

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def read_git_history(repo_path: str, limit: int) -> List[CommitInfo]:
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return []
    shallow: Set[str] = set()
    shallow_file = os.path.join(repo_path, ".git", "shallow")
    if os.path.exists(shallow_file):
        shallow = set(Path(shallow_file).read_text().split())
    try:
        out = subprocess.run(
            ["git", "-C", repo_path, "log", f"-n{limit}", "--no-merges", "--no-color", "--no-ext-diff",
             "-U0", "--pretty=format:%x1e%H%x1f%an%x1f%ae%x1f%at%x1f%s"],
            capture_output=True, text=True, errors="replace", timeout=180, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("git log failed", error=str(e))
        return []

    commits: List[CommitInfo] = []
    for chunk in out.split("\x1e"):
        if not chunk.strip():
            continue
        header, _, patch = chunk.partition("\n")
        parts = header.split("\x1f")
        if len(parts) < 5:
            continue
        sha, author, email, ts, subject = parts[:5]
        info = CommitInfo(sha=sha, author=author, email=email.lower(), timestamp_ms=int(ts) * 1000,
                          message=subject, files={})
        if sha not in shallow:  # a shallow boundary commit diffs against nothing
            _parse_patch(patch, info)
        commits.append(info)
    return commits


def read_merge_commits(repo_path: str, limit: int = 150) -> Dict[str, List[str]]:
    """Merge commit sha -> the commits its pull request brought in (first parent..second parent)."""
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return {}
    try:
        out = subprocess.run(["git", "-C", repo_path, "log", "--merges", f"-n{limit}", "--format=%H %P"],
                             capture_output=True, text=True, timeout=60, check=True).stdout
        merges: Dict[str, List[str]] = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            shas = subprocess.run(["git", "-C", repo_path, "rev-list", "--no-merges", f"{parts[1]}..{parts[2]}"],
                                  capture_output=True, text=True, timeout=30).stdout.split()
            merges[parts[0]] = shas[:100]
        return merges
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("Reading merge commits failed", error=str(e))
        return {}


def _parse_patch(patch: str, info: CommitInfo) -> None:
    current: Optional[str] = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current = target[2:] if target.startswith("b/") else None
            if current:
                info.files.setdefault(current, (0, 0, []))
        elif line.startswith("@@") and current:
            m = _HUNK.match(line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) is not None else 1
                added, removed, ranges = info.files[current]
                ranges.append((start, start + max(count, 1) - 1))
                info.files[current] = (added, removed, ranges)
        elif current and line.startswith("+") and not line.startswith("+++"):
            a, r, ranges = info.files[current]
            info.files[current] = (a + 1, r, ranges)
        elif current and line.startswith("-") and not line.startswith("---"):
            a, r, ranges = info.files[current]
            info.files[current] = (a, r + 1, ranges)


# ---------------------------------------------------------------- entry points

_SAFE_URL = re.compile(r"^https://[A-Za-z0-9.\-]+(:\d+)?(/[A-Za-z0-9._~\-]+)+/?$")


def _validate_remote_url(url: str) -> str:
    if not _SAFE_URL.match(url) or urlparse(url).username:
        raise ValueError("Repository URL must be a plain https:// URL like https://github.com/owner/repo")
    return url


def clone_repository(url: str, dest: str, branch: Optional[str], token: Optional[str]) -> None:
    url = _validate_remote_url(url)
    cmd = ["git"]
    if token:
        # Sent as a header so the token never appears in the URL, argv of git, or logs.
        import base64
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
    cmd += ["clone", "--single-branch", f"--depth={settings.COMMIT_HISTORY_LIMIT}"]
    if branch:
        cmd += ["--branch", branch]
    cmd += ["--", url, dest]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env)
    if result.returncode != 0:
        err = result.stderr.strip().splitlines()[-1:] or ["git clone failed"]
        raise RuntimeError(err[0].replace(token or "\x00", "***"))


def ingest_source(
    source: str,
    graph_name: str,
    repo_id: str,
    repo_name: str,
    branch: Optional[str] = None,
    token: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
) -> Dict[str, int]:
    """Ingest a remote https URL or (if allowed) a local directory."""
    builder = CodeGraphBuilder(graph_name, repo_id, repo_name, source, token or settings.GITHUB_TOKEN)
    if os.path.isdir(source):
        if not settings.ALLOW_LOCAL_REPOS:
            raise ValueError("Local repositories are disabled on this server")
        root = settings.LOCAL_REPOS_ROOT
        if root and os.path.commonpath([os.path.realpath(source), os.path.realpath(root)]) != os.path.realpath(root):
            raise ValueError(f"Local repositories must be inside {root} on this server")
        return builder.ingest(source, progress)
    with tempfile.TemporaryDirectory() as tmp:
        dest = os.path.join(tmp, "repo")
        if progress:
            progress("cloning", {})
        clone_repository(source, dest, branch, token or settings.GITHUB_TOKEN)
        return builder.ingest(dest, progress)
