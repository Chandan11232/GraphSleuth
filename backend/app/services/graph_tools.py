"""Graph tools the agent (and the MCP server) can call.

Each tool runs one or more Cypher queries from `cypher_queries` and returns a
ToolResult carrying the data, a one-line summary, the Cypher that produced it,
and the graph keys it visited (used to draw the evidence graph).
"""
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Set

from app.core.falkordb import get_falkordb
from app.services import cypher_queries as Q

ID_RE = re.compile(r"^[0-9a-f]{16}$")
# Words that carry no meaning in a question to the company brain.
QUESTION_WORDS = set("""why what who whom whose how when where which does did done doing the and for are was were
is isn way ways this that these those with from about our ours we you your they them their use uses used using
should would could can will shall has have had not any all some there here into onto than then also just like
get gets got make makes made thing things know knows tell ask asked""".split())


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

    def test_context(self, function: str) -> ToolResult:
        """Everything needed to write a test for one function: its code, its callers and
        callees, and the nearest existing tests to copy the style of."""
        args = {"function": function}
        fn = self._need(function, "test_context", args)
        if isinstance(fn, ToolResult):
            return fn
        row = self._ro(Q.TEST_CONTEXT, {"id": fn["id"]})[0]
        cypher = [compact(Q.TEST_CONTEXT), compact(Q.NEARBY_TESTS)]
        row["example_tests"] = self._ro(Q.NEARBY_TESTS, {"id": fn["id"]})
        row["examples_are_nearby"] = bool(row["example_tests"])
        if not row["example_tests"]:
            row["example_tests"] = self._ro(Q.ANY_TESTS, {"ext": PurePosixPath(row["file_path"]).suffix})
            cypher.append(compact(Q.ANY_TESTS))
        summary = (f"{row['name']}: {row['caller_count']} caller(s), {len(row['callees'])} callee(s), "
                   f"{len(row['example_tests'])} example test(s)"
                   + ("" if row["examples_are_nearby"] or not row["example_tests"] else " from elsewhere in the repo"))
        return ToolResult("test_context", args, row, summary, cypher, {row["id"]})

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

    # ---------------------------------------------------- company knowledge

    def why_code(self, function: str) -> ToolResult:
        """Why a function is the way it is: the commits that changed it, the pull requests they came
        from (who opened and reviewed them, which issues they fixed), the decisions that govern it and
        whether the code has changed since, and the discussions that name it."""
        args = {"function": function}
        fn = self._need(function, "why_code", args)
        if isinstance(fn, ToolResult):
            return fn
        changes = self._ro(Q.FUNCTION_HISTORY, {"id": fn["id"], "limit": 8})
        numbers = list(dict.fromkeys(c["pr"] for c in changes if c["pr"] is not None))
        prs = {r["number"]: r for r in self._ro(Q.PR_CONTEXT, {"numbers": numbers})} if numbers else {}
        for c in changes:
            c["pull_request"] = prs.get(c.pop("pr"))
        decisions = [d for d in self.decisions(limit=200).data if fn["id"] in d["function_ids"]]
        discussions = self._ro(Q.FUNCTION_DISCUSSIONS, {"id": fn["id"]})
        visited = {fn["id"], *(c["sha"] for c in changes), *(c["email"] for c in changes if c["email"])}
        for pr in prs.values():
            visited.add(pr["id"])
            visited.update(i["id"] for i in pr["fixes"])
        visited.update(d["id"] for d in decisions)
        visited.update(d["id"] for d in discussions)

        parts = []
        latest = changes[0] if changes else None
        if latest and latest["pull_request"]:
            pr = latest["pull_request"]
            approved = [r["name"] for r in pr["reviews"] if r["state"] == "APPROVED"]
            parts.append(f"last changed in PR #{pr['number']} \"{pr['title'][:60]}\" by {pr['opened_by'] or latest['author']}"
                         + (f" (approved by {', '.join(approved)})" if approved else " (no approving review)"))
        elif latest:
            parts.append(f"last changed by {latest['author']}: \"{latest['message'][:60]}\" (no pull request on record)")
        if decisions:
            drifted = [d for d in decisions if d["drift"]]
            parts.append(f"{len(decisions)} decision(s) apply" + (f", {len(drifted)} possibly outdated" if drifted else ""))
        open_items = [d for d in discussions if d["state"] == "open"]
        if open_items:
            parts.append("open: " + ", ".join(f"#{d['number']} {d['title'][:40]}" for d in open_items[:2]))
        return ToolResult("why_code", args, {"function": fn, "changes": changes, "decisions": decisions,
                                             "discussions": discussions},
                          f"{fn['name']}: " + ("; ".join(parts) if parts else "no history on record"),
                          [compact(Q.FUNCTION_HISTORY), compact(Q.PR_CONTEXT), compact(Q.DECISIONS),
                           compact(Q.DECISION_FUNCTIONS), compact(Q.DECISION_DRIFT), compact(Q.FUNCTION_DISCUSSIONS)],
                          visited)

    def decisions(self, limit: int = 50) -> ToolResult:
        rows = self._ro(Q.DECISIONS, {"limit": limit})
        drift: Dict[str, List[Dict[str, Any]]] = {}
        for r in self._ro(Q.DECISION_DRIFT, {}) if rows else []:
            drift.setdefault(r.pop("id"), []).append(r)
        governs: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for r in self._ro(Q.DECISION_FUNCTIONS, {"ids": [d["id"] for d in rows]}) if rows else []:
            governs.setdefault(r["id"], {})[r["fn_id"]] = {"id": r["fn_id"], "name": r["name"]}
        for d in rows:
            fns = governs.get(d["id"], {})
            d["functions"] = list(fns.values())
            d["function_ids"] = list(fns)
            seen = set()
            d["drift"] = []
            for r in drift.get(d["id"], []):  # one line per later commit, naming the functions it changed
                if r["sha"] in seen:
                    next(x for x in d["drift"] if x["sha"] == r["sha"])["functions"].append(r["function"])
                    continue
                seen.add(r["sha"])
                d["drift"].append({**{k: r[k] for k in ("sha", "message", "timestamp", "author", "pr", "reviewers")},
                                   "functions": [r["function"]]})
        drifted = [d for d in rows if d["drift"]]
        return ToolResult("decisions", {"limit": limit}, rows,
                          f"{len(rows)} decision(s) on record" + (f"; {len(drifted)} govern code that has changed since"
                                                                 if drifted else ""),
                          [compact(Q.DECISIONS), compact(Q.DECISION_FUNCTIONS), compact(Q.DECISION_DRIFT)],
                          {d["id"] for d in rows})

    def who_owns(self, target: str) -> ToolResult:
        """Who to ask about a file or function: CODEOWNERS teams and people, authors, and reviewers."""
        args = {"target": target}
        ref = (target or "").strip()
        rows = self._ro(Q.FIND_FILE, {"path": ref, "suffix": "/" + ref.lstrip("/")}) if "/" in ref or "." in ref.split("/")[-1] else []
        fn = None
        if not rows:
            fn = self.resolve(ref)
            if fn is None:
                return ToolResult("who_owns", args, None, f"No file or function matching '{target}'.", error="not_found")
            rows = [{"path": fn["file_path"]}]
        path = rows[0]["path"]
        owners = self._ro(Q.FILE_OWNERS, {"path": path})[0]
        authors = self._ro(Q.FILE_AUTHORS, {"path": path})
        reviewers = self._ro(Q.FILE_REVIEWERS, {"path": path})
        fn_experts = self._ro(Q.FUNCTION_AUTHORS, {"id": fn["id"]}) if fn else []

        people: Dict[str, Dict[str, Any]] = {}

        def add(name: Optional[str], points: float, why: str, email: Optional[str] = None) -> None:
            if not name:
                return
            p = people.setdefault(name, {"name": name, "email": email, "score": 0.0, "reasons": []})
            p["score"] += points
            p["email"] = p["email"] or email
            if why not in p["reasons"]:
                p["reasons"].append(why)

        for t in owners["teams"]:
            for m in t["members"]:
                add(m, 3, f"in @{t['team']}, which owns {t['pattern']}")
        for o in owners["people"]:
            add(o["name"], 4, f"code owner of {o['pattern']}", o["email"])
        for e in fn_experts:
            add(e["name"], 1.5 * e["fn_commits"], f"changed {fn['name']} {e['fn_commits']}x", e["email"])
        for a in authors:
            add(a["name"], a["commits"], f"{a['commits']} commit(s) to the file", a["email"])
        for r in reviewers:
            add(r["name"], 1.5 * r["reviews"], "reviewed " + ", ".join(f"#{n}" for n in r["prs"]), r["email"])
        ranked = sorted(people.values(), key=lambda p: -p["score"])
        for p in ranked:
            p["score"] = round(p["score"], 1)
        data = {"file": path, "function": fn, "teams": owners["teams"], "owners": owners["people"],
                "authors": authors, "reviewers": reviewers, "ask": ranked[:6]}
        teams = ", ".join("@" + t["team"] for t in owners["teams"])
        summary = (f"{path}: " + (f"owned by {teams}; " if teams else "no CODEOWNERS entry; ")
                   + ("ask " + ", ".join(p["name"] for p in ranked[:3]) if ranked else "nobody on record"))
        visited = {*(t["id"] for t in owners["teams"]), *(p["email"] for p in ranked if p["email"])}
        if fn:
            visited.add(fn["id"])
        return ToolResult("who_owns", args, data, summary,
                          [compact(Q.FILE_OWNERS), compact(Q.FILE_AUTHORS), compact(Q.FILE_REVIEWERS)]
                          + ([compact(Q.FUNCTION_AUTHORS)] if fn else []), visited)

    def search_knowledge(self, query: str, limit: int = 6) -> ToolResult:
        """Full-text search over issues, pull requests, comments and decisions."""
        words = [w for w in re.findall(r"[A-Za-z0-9_]+", query.lower()) if len(w) > 2 and w not in QUESTION_WORDS]
        q = "|".join(dict.fromkeys(words))
        rows: List[Dict[str, Any]] = []
        queries = (Q.SEARCH_DECISIONS, Q.SEARCH_PULLS, Q.SEARCH_ISSUES, Q.SEARCH_COMMENTS)
        if q:
            for cypher in queries:
                try:
                    found = self._ro(cypher, {"q": q, "limit": limit})
                except Exception:  # the index does not exist until knowledge has been loaded
                    found = []
                top = max((r["score"] for r in found), default=1.0) or 1.0
                stems = {_stem(w) for w in words}
                for r in found:
                    text_stems = {_stem(w) for w in re.findall(r"[A-Za-z0-9_]+", f"{r['title']} {r['text']}".lower())}
                    r["matched_words"] = sorted(stems & text_stems)
                    # BM25 scores are not comparable across indexes; word coverage is.
                    r["score"] = round(0.3 * r["score"] / top + 0.7 * len(r["matched_words"]) / max(len(stems), 1), 3)
                    r["text"] = (r["text"] or "")[:600]
                rows += found
        rows.sort(key=lambda r: (-r["score"], -(r["at_ms"] or 0)))
        visited = {r.get("item_id") or r["id"] for r in rows[: limit * 2]} | {r["id"] for r in rows[: limit * 2]}
        return ToolResult("search_knowledge", {"query": query}, rows[: limit * 2],
                          f"{len(rows)} matching issue(s), pull request(s), comment(s) and decision(s)"
                          + (f"; best: {rows[0]['type']} #{rows[0]['number']} \"{rows[0]['title'][:50]}\"" if rows else ""),
                          [compact(c) for c in queries], visited)

    def knowledge_gaps(self, limit: int = 10) -> ToolResult:
        rows = self._ro(Q.SINGLE_AUTHOR_FILES, {"limit": limit})
        unowned = [r for r in rows if not r["owners"]]
        return ToolResult("knowledge_gaps", {"limit": limit}, rows,
                          f"{len(rows)} file(s) only one person has changed; {len(unowned)} of them have no code owner",
                          [compact(Q.SINGLE_AUTHOR_FILES)])

    def ownership(self) -> ToolResult:
        rows = self._ro(Q.OWNERSHIP_MAP, {})
        return ToolResult("ownership", {}, rows, f"{len(rows)} ownership rule(s)", [compact(Q.OWNERSHIP_MAP)])

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
    {"name": "why_code", "description": "Why a function is the way it is: the pull requests that changed it, who "
     "reviewed them, the issues they fixed, decisions that govern it (and whether code changed since), open issues "
     "that mention it.",
     "parameters": {"function": {"type": "string"}}, "required": ["function"]},
    {"name": "who_owns", "description": "Who to ask about a file or function: CODEOWNERS teams, authors and reviewers.",
     "parameters": {"target": {"type": "string", "description": "file path or function name"}}, "required": ["target"]},
    {"name": "search_knowledge", "description": "Full-text search over issues, pull requests, review comments and "
     "recorded decisions.",
     "parameters": {"query": {"type": "string"}}, "required": ["query"]},
    {"name": "recall_past_investigations", "description": "Earlier bug investigations that involved these functions.",
     "parameters": {"functions": {"type": "array", "items": {"type": "string"}}}, "required": ["functions"]},
]


def run_tool(tools: GraphTools, name: str, args: Dict[str, Any], exclude: str = "") -> ToolResult:
    args = dict(args or {})
    try:
        if name == "search_code":
            return tools.search_code(str(args.get("query", "")))
        if name == "search_knowledge":
            return tools.search_knowledge(str(args.get("query", "")))
        if name == "who_owns":
            return tools.who_owns(str(args.get("target") or args.get("function") or ""))
        if name in ("get_function", "find_experts", "blast_radius", "why_code"):
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
