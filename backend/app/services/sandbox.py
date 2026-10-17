"""A throwaway copy of a repository where a fix is applied and its tests are run.

The copy is made from the directory the repository was connected from, or by a
shallow clone of its URL. Tests run in a child process with:

* a scrubbed environment: no FalkorDB, GitHub or LLM credentials reach the repo's code,
  and HOME points into the sandbox,
* a wall-clock timeout that kills the whole process group,
* CPU, file-size and (on Linux) memory limits,
* its own working directory, deleted afterwards.

Running a repository's tests runs its code. That is why VERIFY_FIXES defaults to
"local": only repositories connected from a server directory, which the operator
put there, can be verified. Set it to "all" only where the API runs in a
disposable container.
"""
import ast
import builtins
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.code_graph import clone_repository

IGNORE = shutil.ignore_patterns("node_modules", "venv", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache",
                                "dist", "build", ".tox")


class SandboxError(Exception):
    pass


def verification_allowed(source: str) -> Optional[str]:
    """None if this server may run the repository's tests, else the reason it may not."""
    mode = settings.VERIFY_FIXES.lower()
    if mode == "off":
        return "Fix verification is turned off on this server (VERIFY_FIXES=off)."
    if os.path.isdir(source):
        return None
    if mode == "all":
        return None
    return ("This server only verifies repositories connected from a server directory (VERIFY_FIXES=local), "
            "because running a repository's tests runs its code. Self-host with VERIFY_FIXES=all in a "
            "disposable container to verify GitHub repositories.")


@dataclass
class Workspace:
    root: str
    base: str
    head: str = ""
    patched_files: Dict[str, str] = field(default_factory=dict)
    original_files: Dict[str, str] = field(default_factory=dict)

    def path(self, rel: str) -> str:
        full = os.path.realpath(os.path.join(self.root, rel))
        if os.path.commonpath([full, os.path.realpath(self.root)]) != os.path.realpath(self.root):
            raise SandboxError(f"Path escapes the sandbox: {rel}")
        return full

    def read(self, rel: str) -> str:
        with open(self.path(rel), encoding="utf-8") as fh:
            return fh.read()

    def write(self, rel: str, text: str) -> None:
        full = self.path(rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def cleanup(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def prepare(source: str, branch: Optional[str] = None, token: Optional[str] = None) -> Workspace:
    # realpath: on macOS the temp dir is behind a /var -> /private/var symlink, which confuses pytest's rootdir.
    base = os.path.realpath(tempfile.mkdtemp(prefix="cd-sandbox-"))
    root = os.path.join(base, "repo")
    try:
        if os.path.isdir(source):
            shutil.copytree(source, root, ignore=IGNORE, symlinks=True)
        else:
            clone_repository(source, root, branch, token or settings.GITHUB_TOKEN)
    except Exception as e:
        shutil.rmtree(base, ignore_errors=True)
        raise SandboxError(f"Could not copy the repository: {str(e)[:200]}") from e
    os.makedirs(os.path.join(base, "home"), exist_ok=True)
    ws = Workspace(root=root, base=base)
    if os.path.isdir(os.path.join(root, ".git")):
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True)
        ws.head = out.stdout.strip()
    return ws


def splice_function(ws: Workspace, file_path: str, old_code: str, new_code: str, start_line: int) -> str:
    """Replace one function in a file with its corrected version and return the file's unified diff.

    The graph stores each function's exact source and line span. If the file has moved on since
    ingestion, the function is looked up by its source instead of its line numbers."""
    text = ws.read(file_path)
    ws.original_files.setdefault(file_path, text)
    lines = text.splitlines(keepends=True)
    old_lines = old_code.rstrip("\n").splitlines()
    n = len(old_lines)

    def matches(at: int) -> bool:
        window = [ln.rstrip("\r\n") for ln in lines[at:at + n]]
        return [w.rstrip() for w in window] == [o.rstrip() for o in old_lines]

    at = start_line - 1
    if not (0 <= at < len(lines) and matches(at)):
        found = [i for i in range(len(lines)) if matches(i)]
        if len(found) != 1:
            raise SandboxError(f"{file_path} no longer contains this version of the function. The graph is older "
                               "than the code: sync the repository and investigate again.")
        at = found[0]
    eol = "\r\n" if lines[at].endswith("\r\n") else "\n"
    new_lines = [ln + eol for ln in new_code.rstrip("\n").splitlines()]
    patched = "".join(lines[:at] + new_lines + lines[at + n:])
    ws.write(file_path, patched)
    ws.patched_files[file_path] = patched
    return "".join(difflib.unified_diff(text.splitlines(keepends=True), patched.splitlines(keepends=True),
                                        f"a/{file_path}", f"b/{file_path}"))


def file_diff(ws: Workspace, file_path: str) -> str:
    return "".join(difflib.unified_diff(ws.original_files[file_path].splitlines(keepends=True),
                                        ws.read(file_path).splitlines(keepends=True),
                                        f"a/{file_path}", f"b/{file_path}"))


def check_python(text: str, file_path: str) -> None:
    try:
        ast.parse(text)
    except SyntaxError as e:
        raise SandboxError(f"The patched {file_path} is not valid Python: {e.msg} (line {e.lineno})") from e


def undefined_names(text: str) -> List[str]:
    """Names the module reads that it never defines, imports or gets from builtins: usually an
    import a patch needed but did not add (a patch only rewrites one function)."""
    tree = ast.parse(text)
    defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    loaded: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            defined |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
            if not isinstance(node, ast.ClassDef):
                args = node.args
                defined |= {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
                defined |= {a.arg for a in (args.vararg, args.kwarg) if a}
        elif isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                defined.add(node.id)
            else:
                loaded.append(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            defined |= set(node.names)
    return list(dict.fromkeys(n for n in loaded if n not in defined))


def module_for(file_path: str) -> str:
    p = PurePosixPath(file_path)
    parts = list(p.with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def add_import(ws: Workspace, file_path: str, module: str, name: str) -> str:
    """Add `name` to an existing single-line `from module import ...`, or a new import after the last one."""
    text = ws.read(file_path)
    pattern = re.compile(rf"^(from\s+{re.escape(module)}\s+import\s+)([^()\n#]+?)(\s*(?:#.*)?)$", re.M)
    m = pattern.search(text)
    if m:
        text = text[:m.start()] + f"{m.group(1)}{m.group(2).rstrip()}, {name}{m.group(3)}" + text[m.end():]
        how = f"added {name} to `from {module} import ...`"
    else:
        tree = ast.parse(text)
        imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        lines = text.splitlines(keepends=True)
        at = imports[-1].end_lineno if imports else 0
        lines.insert(at, f"from {module} import {name}\n")
        text = "".join(lines)
        how = f"added `from {module} import {name}`"
    ws.write(file_path, text)
    ws.patched_files[file_path] = text
    return how


def apply_diff(ws: Workspace, diff: str) -> str:
    """Apply a unified diff with git, for clients that send a patch rather than the corrected function."""
    patch_file = os.path.join(ws.base, "fix.patch")
    with open(patch_file, "w", encoding="utf-8") as fh:
        fh.write(diff if diff.endswith("\n") else diff + "\n")
    files = [ln[6:].strip() for ln in diff.splitlines() if ln.startswith("+++ b/")]
    for f in files:
        try:
            ws.original_files.setdefault(f, ws.read(f))
        except FileNotFoundError:
            ws.original_files.setdefault(f, "")
    res = subprocess.run(["git", "apply", "--recount", "--whitespace=nowarn", patch_file], cwd=ws.root,
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise SandboxError(f"The patch does not apply: {(res.stderr or res.stdout).strip()[:300]}")
    for f in files:
        ws.patched_files[f] = ws.read(f)
    return diff


def _limits() -> None:  # runs in the child before exec
    import resource
    cpu = settings.VERIFY_TIMEOUT_SECONDS
    for name, value in (("RLIMIT_CPU", cpu), ("RLIMIT_FSIZE", 64 * 1024 * 1024), ("RLIMIT_CORE", 0)):
        try:
            resource.setrlimit(getattr(resource, name), (value, value))
        except (ValueError, OSError, AttributeError):
            pass
    if sys.platform.startswith("linux"):
        try:
            mem = settings.VERIFY_MEMORY_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
        except (ValueError, OSError):
            pass


def _env(ws: Workspace) -> Dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.path.join(ws.base, "home"),
        "TMPDIR": os.path.join(ws.base, "home"),
        "PYTHONPATH": ws.root,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "LANG": "C.UTF-8",
        "CI": "1",
        # Plugins installed for the API itself must not change how the repository's tests run.
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


def pytest_node_id(file_path: str, name: str) -> str:
    return f"{file_path}::{name.replace('.', '::')}"


def run_pytest(ws: Workspace, node_ids: List[str]) -> Dict[str, Any]:
    """Run the given pytest node ids and return one outcome per test."""
    report = os.path.join(ws.base, f"report-{time.time_ns()}.xml")
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "-o", "junit_family=xunit1", "--color=no",
           f"--junitxml={report}", "--rootdir", ws.root, *node_ids]
    started = time.time()
    proc = subprocess.Popen(cmd, cwd=ws.root, env=_env(ws), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, start_new_session=True, preexec_fn=_limits)
    timed_out = False
    try:
        output, _ = proc.communicate(timeout=settings.VERIFY_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, 9)
        except ProcessLookupError:
            pass
        output, _ = proc.communicate()
    duration_ms = int((time.time() - started) * 1000)
    tests = _parse_junit(report, ws.root) if os.path.exists(report) else []
    counts = {k: sum(t["outcome"] == k for t in tests) for k in ("passed", "failed", "error", "skipped")}
    collection_errors = [t["message"] for t in tests if t["outcome"] == "collection_error"]
    tests = [t for t in tests if t["outcome"] != "collection_error"]
    return {
        "command": "python -m pytest " + " ".join(node_ids),
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "tests": tests,
        **counts,
        "collection_errors": collection_errors,
        "output": (output or "")[-4000:],
    }


def _parse_junit(path: str, root: str) -> List[Dict[str, Any]]:
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return []
    out = []
    for case in tree.iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        if not name:  # a collection error, reported against a module or package rather than a test
            el = case.find("error")
            out.append({"name": "", "file_path": "", "outcome": "collection_error", "duration_ms": 0,
                        "message": ((el.get("message") or el.text or "") if el is not None else "")[:600]})
            continue
        # classname is "tests.test_refunds" or "tests.test_refunds.TestRefunds".
        parts = classname.split(".")
        file_path, cls = case.get("file"), []
        for i in range(len(parts), 0, -1):
            candidate = "/".join(parts[:i]) + ".py"
            if os.path.exists(os.path.join(root, candidate)):
                file_path, cls = file_path or candidate, parts[i:]
                break
        outcome, message = "passed", ""
        for tag in ("failure", "error", "skipped"):
            el = case.find(tag)
            if el is not None:
                outcome = {"failure": "failed", "error": "error", "skipped": "skipped"}[tag]
                message = (el.get("message") or el.text or "")[:600]
                break
        out.append({
            "name": ".".join(cls + [name]),
            "file_path": file_path or str(PurePosixPath(*parts)) + ".py",
            "outcome": outcome,
            "message": message,
            "duration_ms": int(float(case.get("time") or 0) * 1000),
        })
    return out
