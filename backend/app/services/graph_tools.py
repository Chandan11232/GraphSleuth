"""Graph tools the agent (and the MCP server) can call.

Each tool runs one or more Cypher queries from `cypher_queries` and returns a
ToolResult carrying the data, a one-line summary, the Cypher that produced it,
and the graph keys it visited (used to draw the evidence graph).
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.core.falkordb import get_falkordb
from app.services import cypher_queries as Q

ID_RE = re.compile(r"^[0-9a-f]{16}$")


def _stem(word: str) -> str:
    """Crude stemmer so 'tasks'/'task' and 'appended'/'append' match."""
    w = word.lower()
    for suffix in ("ing", "ed", "es", "s"):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def compact(cypher: str) -> str:
    lines = [ln.rstrip() for ln in cypher.strip().splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


@dataclass
class ToolResult:
    tool: str
    args: Dict[str, Any]
    data: Any
    summary: str
    cypher: List[str] = field(default_factory=list)
    visited: Set[str] = field(default_factory=set)
    error: Optional[str] = None


class GraphTools:
    def __init__(self, graph_name: str):
        self.graph = graph_name
        self.db = get_falkordb()

    def _ro(self, cypher: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return self.db.ro(self.graph, cypher, params)

    # ------------------------------------------------------------ resolving

    def resolve(self, ref: str, prefer: Optional[Set[str]] = None) -> Optional[Dict[str, Any]]:
        """Accepts a function id, a qualified name (Class.method) or a bare name."""
        raw = (ref or "").strip().strip("`")
        ref = raw.strip("'\"")
        if not ref:
            return None
        if ref != raw.strip("`'\"()") and not ID_RE.match(ref):
            # Names like form.addEventListener('submit') contain quotes and parentheses.
            rows = self._ro(Q.SEARCH_EXACT_NAME, {"names": [ref], "limit": 5})
            if rows:
                return rows[0]
        ref = raw.strip("`'\"()")
        if ID_RE.match(ref):
            rows = self._ro("MATCH (f:Function {id: $id}) RETURN f.id AS id, f.qualified_name AS name, f.file_path AS file_path", {"id": ref})
            if rows:
                return rows[0]
        names = [ref, ref.split(".")[-1]]
        rows = self._ro(Q.SEARCH_EXACT_NAME, {"names": names, "limit": 20})
        if not rows and ":" in ref:  # "path/to/file.py:func"
            path, _, name = ref.rpartition(":")
            rows = self._ro(Q.SEARCH_STACK_FRAME, {"name": name, "file_suffix": path, "line": -1})
        if not rows:
            return None
        exact = [r for r in rows if r["name"] == ref] or rows
        if prefer:
            preferred = [r for r in exact if r["id"] in prefer]
            if preferred:
                return preferred[0]
        return exact[0]

    def _need(self, ref: str, tool: str, args: Dict[str, Any]) -> Any:
        fn = self.resolve(ref)
        if fn is None:
            return ToolResult(tool, args, None, f"No function matching '{ref}' in the graph.", error="not_found")
        return fn

    # ---------------------------------------------------------------- tools

    def search_code(self, query: str, limit: int = 8) -> ToolResult:
        words = [w for w in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(w) > 2]
        ft_query = "|".join(dict.fromkeys(words)) or query
        rows = self._ro(Q.SEARCH_FULLTEXT, {"q": ft_query, "limit": max(limit, 15)}) if words else []
        # BM25 favours short functions. Also score each hit by the report words it
        # contains, weighting a word by how rare it is among the hits (a word that
        # every candidate contains, like the file name, tells us nothing).
        stems = {_stem(w) for w in words}
        top = max((r["score"] for r in rows), default=1.0) or 1.0
        matched = [stems & {_stem(w) for w in r.pop("text", "").split()} for r in rows]
        df = {w: sum(w in m for m in matched) for w in stems}
        weights = [sum(1.0 / df[w] for w in m) for m in matched]
        best = max(weights, default=1.0) or 1.0
        for r, m, wt in zip(rows, matched, weights):
            r["matched_words"] = sorted(m)
            r["coverage"] = round(wt / best, 3)
            r["score"] = round(0.2 * r["score"] / top + 0.8 * r["coverage"], 3)
        rows = sorted(rows, key=lambda r: r["score"], reverse=True)[:limit]
        return ToolResult(
            "search_code", {"query": query}, rows,
            f"{len(rows)} functions match" + (f"; best: {', '.join(r['name'] for r in rows[:3])}" if rows else ""),
            [compact(Q.SEARCH_FULLTEXT)], {r["id"] for r in rows},
        )

    def get_function(self, function: str) -> ToolResult:
        args = {"function": function}
        fn = self._need(function, "get_function", args)
        if isinstance(fn, ToolResult):
            return fn
        rows = self._ro(Q.GET_FUNCTION, {"id": fn["id"]})
        row = rows[0]
        return ToolResult("get_function", args, row,
                          f"{row['name']} at {row['file_path']}:{row['start_line']}-{row['end_line']}",
                          [compact(Q.GET_FUNCTION)], {row["id"]})

    def get_callers(self, function: str, depth: int = 2) -> ToolResult:
        args = {"function": function, "depth": depth}
        fn = self._need(function, "get_callers", args)
        if isinstance(fn, ToolResult):
            return fn
        depth = max(1, min(int(depth or 2), 3))
        rows = self._ro(Q.GET_CALLERS, {"id": fn["id"], "depth": depth, "limit": 25})
        return ToolResult("get_callers", args, {"function": fn, "callers": rows},
                          f"{len(rows)} functions reach {fn['name']} within {depth} call(s)"
                          + (f": {', '.join(r['name'] for r in rows[:4])}" if rows else ""),
                          [compact(Q.GET_CALLERS)], {fn["id"], *(r["id"] for r in rows)})

    def get_callees(self, function: str, depth: int = 1) -> ToolResult:
        args = {"function": function, "depth": depth}
        fn = self._need(function, "get_callees", args)
        if isinstance(fn, ToolResult):
            return fn
        depth = max(1, min(int(depth or 1), 3))
        rows = self._ro(Q.GET_CALLEES, {"id": fn["id"], "depth": depth, "limit": 25})
        return ToolResult("get_callees", args, {"function": fn, "callees": rows},
                          f"{fn['name']} calls {len(rows)} repo functions within {depth} hop(s)"
                          + (f": {', '.join(r['name'] for r in rows[:5])}" if rows else ""),
                          [compact(Q.GET_CALLEES)], {fn["id"], *(r["id"] for r in rows)})

    def get_tests(self, functions: List[str]) -> ToolResult:
        ids = self._ids(functions)
        rows = self._ro(Q.GET_TESTS, {"ids": ids}) if ids else []
        untested = [r["name"] for r in rows if not r["direct_tests"] and not r["indirect_tests"]]
        visited = set(ids)
        for r in rows:
            visited.update(t["id"] for t in r["direct_tests"] + r["indirect_tests"])
        return ToolResult("get_tests", {"functions": functions}, rows,
                          f"{len(rows) - len(untested)}/{len(rows)} functions have tests"
                          + (f"; untested: {', '.join(untested[:5])}" if untested else ""),
                          [compact(Q.GET_TESTS)], visited)

    def recent_changes(self, functions: List[str], per_function: int = 3) -> ToolResult:
        ids = self._ids(functions)
        rows = self._ro(Q.GET_RECENT_CHANGES, {"ids": ids, "per_fn": per_function}) if ids else []
        recency = self._ro(Q.GET_CHANGE_RECENCY, {"ids": ids}) if ids else []
        visited = set(ids)
        changed = []
        for r in rows:
            for c in r["commits"]:
                visited.update({c["sha"], c["email"]})
            if r["commits"]:
                changed.append((r["commits"][0]["timestamp"], r["name"], r["commits"][0]))
        changed = [f"{name} ({c['author']}: \"{c['message'][:50]}\")" for _, name, c in sorted(changed, key=lambda x: x[0], reverse=True)]
        return ToolResult("recent_changes", {"functions": functions}, {"changes": rows, "recency": recency},
                          f"{len(changed)}/{len(rows)} functions have commit history"
                          + (f"; most recent: {changed[0]}" if changed else ""),
                          [compact(Q.GET_RECENT_CHANGES), compact(Q.GET_CHANGE_RECENCY)], visited)

    def find_experts(self, function: str) -> ToolResult:
        args = {"function": function}
        fn = self._need(function, "find_experts", args)
        if isinstance(fn, ToolResult):
            return fn
        rows = self._ro(Q.FIND_EXPERTS, {"id": fn["id"]})
        return ToolResult("find_experts", args, rows,
                          f"People who changed {fn['name']}: " + (", ".join(r["name"] for r in rows) or "none on record"),
                          [compact(Q.FIND_EXPERTS)], {fn["id"], *(r["email"] for r in rows)})

    def blast_radius(self, function: str) -> ToolResult:
        args = {"function": function}
        fn = self._need(function, "blast_radius", args)
        if isinstance(fn, ToolResult):
            return fn
        row = self._ro(Q.BLAST_RADIUS, {"id": fn["id"]})[0]
        row["affected_files"] = len(set(row.pop("caller_files")))
        return ToolResult("blast_radius", args, row,
                          f"Changing {fn['name']} affects {row['affected_functions']} functions in "
                          f"{row['affected_files']} files; {len(row['covering_tests'])} tests would exercise it",
                          [compact(Q.BLAST_RADIUS)], {fn["id"]})

    def call_path(self, from_function: str, to_function: str) -> ToolResult:
        args = {"from_function": from_function, "to_function": to_function}
        a = self._need(from_function, "call_path", args)
        if isinstance(a, ToolResult):
            return a
        b = self._need(to_function, "call_path", args)
        if isinstance(b, ToolResult):
            return b
        rows = self._ro(Q.CALL_PATH, {"src": a["id"], "dst": b["id"]})
        path = rows[0]["path"] if rows else []
        return ToolResult("call_path", args, path,
                          (" -> ".join(p["name"] for p in path)) if path else f"No call path from {a['name']} to {b['name']}",
                          [compact(Q.CALL_PATH)], {p["id"] for p in path})

    def pagerank_available(self) -> bool:
        """algo.pageRank fails on a graph with no CALLS edges (LAGr error -6)."""
        return bool(self._ro("MATCH ()-[r:CALLS]->() RETURN count(r) AS n", {})[0]["n"])

    def hotspots(self, limit: int = 10) -> ToolResult:
        if not self.pagerank_available():
            return ToolResult("hotspots", {"limit": limit}, [], "No call graph to rank (no CALLS edges)",
                              [compact(Q.HOTSPOTS)])
        rows = self._ro(Q.HOTSPOTS, {"limit": limit})
        return ToolResult("hotspots", {"limit": limit}, rows,
                          "Most central functions (PageRank on CALLS): " + ", ".join(r["name"] for r in rows[:5]),
                          [compact(Q.HOTSPOTS)], {r["id"] for r in rows})

    def risky_changes(self, commits: int = 10) -> ToolResult:
        commits = max(1, min(int(commits or 10), 300))
        rows = self._ro(Q.RISKY_CHANGES, {"n": commits})
        summary = f"{len(rows)} untested function(s) changed in the last {commits} commits"
        if rows:
            summary += ": " + ", ".join(f"{r['function']} ({', '.join(r['changed_by'])})" for r in rows[:5])
        return ToolResult("risky_changes", {"commits": commits}, rows, summary,
                          [compact(Q.RISKY_CHANGES)], {r["id"] for r in rows})

    def recall_past_investigations(self, functions: Optional[List[str]] = None, text: str = "",
                                   exclude: str = "") -> ToolResult:
        ids = self._ids(functions or [])
        by_fn = self._ro(Q.RECALL_BY_FUNCTIONS, {"ids": ids, "exclude": exclude}) if ids else []
        words = [w for w in re.findall(r"[A-Za-z0-9_]+", text.lower()) if len(w) > 3]
        by_text = self._ro(Q.RECALL_BY_TEXT, {"q": "|".join(dict.fromkeys(words)), "exclude": exclude}) if words else []
        cypher = [compact(Q.RECALL_BY_FUNCTIONS)] + ([compact(Q.RECALL_BY_TEXT)] if words else [])
        n = len({r["investigation_id"] for r in by_fn} | {r["investigation_id"] for r in by_text})
        detail = ""
        confirmed = [r for r in by_fn if r["relation"] == "CONFIRMED"]
        if confirmed:
            detail = f"; {confirmed[0]['past_fn']} was the confirmed root cause of \"{confirmed[0]['bug_title']}\""
        elif by_text and by_text[0]["confirmed"]:
            detail = f"; similar bug \"{by_text[0]['bug_title']}\" was caused by {by_text[0]['confirmed'][0]}"
        return ToolResult("recall_past_investigations", {"functions": functions or [], "text": text[:80]},
                          {"by_function": by_fn, "by_text": by_text},
                          f"{n} related past investigation(s) in memory" + detail, cypher,
                          {r["past_fn_id"] for r in by_fn})

    # ------------------------------------------------------------- internal

    def _ids(self, refs: List[str]) -> List[str]:
        out = []
        for ref in refs or []:
            fn = self.resolve(ref)
            if fn and fn["id"] not in out:
                out.append(fn["id"])
        return out


# JSON-schema descriptions shared by the LLM agent and the MCP server.
TOOL_SPECS: List[Dict[str, Any]] = [
    {"name": "search_code", "description": "Full-text search for functions by words from the bug report.",
     "parameters": {"query": {"type": "string", "description": "words or identifiers"}}, "required": ["query"]},
    {"name": "get_function", "description": "Read a function's source code, signature and location.",
     "parameters": {"function": {"type": "string", "description": "function name or id"}}, "required": ["function"]},
    {"name": "get_callers", "description": "Functions that call this function, directly or up to `depth` hops away.",
     "parameters": {"function": {"type": "string"}, "depth": {"type": "integer", "description": "1-3"}}, "required": ["function"]},
    {"name": "get_callees", "description": "Repository functions this function calls (what it depends on).",
     "parameters": {"function": {"type": "string"}, "depth": {"type": "integer", "description": "1-3"}}, "required": ["function"]},
    {"name": "get_tests", "description": "Tests that cover the given functions directly or through callers.",
     "parameters": {"functions": {"type": "array", "items": {"type": "string"}}}, "required": ["functions"]},
    {"name": "recent_changes", "description": "Recent commits (with authors) that changed the given functions.",
     "parameters": {"functions": {"type": "array", "items": {"type": "string"}}}, "required": ["functions"]},
    {"name": "find_experts", "description": "People who have changed this function or its file.",
     "parameters": {"function": {"type": "string"}}, "required": ["function"]},
    {"name": "blast_radius", "description": "What breaks if this function changes: transitive callers, files, tests.",
     "parameters": {"function": {"type": "string"}}, "required": ["function"]},
    {"name": "call_path", "description": "Shortest call chain from one function to another.",
     "parameters": {"from_function": {"type": "string"}, "to_function": {"type": "string"}},
     "required": ["from_function", "to_function"]},
    {"name": "hotspots", "description": "Most central functions in the codebase (PageRank over the call graph).",
     "parameters": {"limit": {"type": "integer"}}, "required": []},
    {"name": "risky_changes", "description": "Untested functions changed by the most recent commits, with who changed "
     "them. Good for 'it broke recently' bugs.",
     "parameters": {"commits": {"type": "integer", "description": "how many recent commits to look at"}}, "required": []},
    {"name": "recall_past_investigations", "description": "Earlier bug investigations that involved these functions.",
     "parameters": {"functions": {"type": "array", "items": {"type": "string"}}}, "required": ["functions"]},
]


def run_tool(tools: GraphTools, name: str, args: Dict[str, Any], exclude: str = "") -> ToolResult:
    args = dict(args or {})
    try:
        if name == "search_code":
            return tools.search_code(str(args.get("query", "")))
        if name in ("get_function", "find_experts", "blast_radius"):
            return getattr(tools, name)(str(args.get("function", "")))
        if name in ("get_callers", "get_callees"):
            return getattr(tools, name)(str(args.get("function", "")), int(args.get("depth") or (2 if name == "get_callers" else 1)))
        if name in ("get_tests", "recent_changes"):
            fns = args.get("functions") or args.get("function") or []
            return getattr(tools, name)([fns] if isinstance(fns, str) else list(fns))
        if name == "call_path":
            return tools.call_path(str(args.get("from_function", "")), str(args.get("to_function", "")))
        if name == "hotspots":
            return tools.hotspots(int(args.get("limit") or 10))
        if name == "risky_changes":
            return tools.risky_changes(int(args.get("commits") or 10))
        if name == "recall_past_investigations":
            fns = args.get("functions") or []
            return tools.recall_past_investigations([fns] if isinstance(fns, str) else list(fns), exclude=exclude)
    except Exception as e:
        return ToolResult(name, args, None, f"Tool error: {e}", error=str(e))
    return ToolResult(name, args, None, f"Unknown tool '{name}'", error="unknown_tool")
