"""The investigation agent.

1. Parse the bug report (identifiers, stack frames, keywords).
2. Locate entry points in the graph (stack frames > exact names > full-text).
3. Expand along CALLS edges (callers and callees).
4. Gather evidence for every candidate: tests, commit history, PageRank,
   complexity, and memory of past investigations.
5. Rank suspects with an explainable score.
6. If an LLM is available, let it drive further graph tools and pick the root
   cause; otherwise the top-ranked suspect is the verdict.
7. Write a patch from the real source, and save the investigation to memory.

Every step is emitted as an event carrying the Cypher that produced it.
"""
import difflib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

import structlog

from app.core.config import settings
from app.core.falkordb import get_falkordb
from app.services import cypher_queries as Q
from app.services.graph_tools import TOOL_SPECS, GraphTools, ToolResult, compact, run_tool
from app.services.llm import LLMUnavailable, ToolsUnsupported, get_llm

logger = structlog.get_logger()

WEIGHTS = {
    "relevance": 0.40,
    "recent_change": 0.20,
    "untested": 0.12,
    "memory": 0.15,
    "complexity": 0.08,
    "centrality": 0.05,
}

STOPWORDS = set("""a an the and or but if then else when while for to of in on at by with from into onto is are was
were be been being it its this that these those there here we you they he she i me my our your their them us do does
did doing have has had having can could should would will shall may might must not no yes so than too very just also
only after before again error errors bug bugs issue fails failed failing failure crash crashes crashed broken wrong
expected actual instead happens happen happening sometimes always never users user when get gets got getting returns
return returned line file traceback most recent call last raise raised exception see seeing""".split())

PY_FRAME = re.compile(r'File "([^"]+)", line (\d+), in ([A-Za-z_<>][\w<>]*)')
JS_FRAME = re.compile(r"at (?:async )?([\w$.<>]+) \(?([^\s():]+):(\d+)(?::\d+)?\)?")
GO_JAVA_FRAME = re.compile(r"([\w$]+)\(([\w/.\-]+\.(?:go|java)):(\d+)\)")
IDENT = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\b")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Investigation:
    def __init__(self, graph_name: str, repo_id: str, investigation_id: str, bug_id: str,
                 title: str, description: str, use_llm: bool = True):
        self.graph = graph_name
        self.repo_id = repo_id
        self.id = investigation_id
        self.bug_id = bug_id
        self.title = title
        self.description = description
        self.text = f"{title}\n{description}"
        self.use_llm = use_llm
        self.tools = GraphTools(graph_name)
        self.db = get_falkordb()
        self.llm = get_llm()

        self.steps: List[Dict[str, Any]] = []
        self.visited: Set[str] = set()
        self.entry_ids: List[str] = []
        self.candidates: Dict[str, Dict[str, Any]] = {}
        self.suspects: List[Dict[str, Any]] = []
        self.verdict: Dict[str, Any] = {}
        self.fix: Dict[str, Any] = {}
        self.keywords: List[str] = []
        self.mode = "graph"
        self.created_at = now_iso()

    # --------------------------------------------------------------- events

    def _step(self, action: str, reasoning: str, result_summary: str, queries: Optional[List[str]] = None,
              tool: Optional[ToolResult] = None, actor: str = "agent", data: Any = None) -> Dict[str, Any]:
        if tool is not None:
            self.visited |= tool.visited
        step = {
            "step": len(self.steps) + 1,
            "action": action,
            "actor": actor,
            "tool": tool.tool if tool else None,
            "args": tool.args if tool else None,
            "query": "\n\n".join(queries if queries is not None else (tool.cypher if tool else [])),
            "result_summary": result_summary,
            "reasoning": reasoning,
            "data": data,
            "timestamp": now_iso(),
        }
        self.steps.append(step)
        return {"type": "step", "step": step}

    # ------------------------------------------------------------------ run

    async def run(self) -> AsyncIterator[Dict[str, Any]]:
        started = time.time()
        self._save_bug_report("analyzing")
        yield {"type": "status", "status": "running", "message": "Reading the bug report"}

        frames, identifiers, words = self._parse_report()
        self.keywords = list(dict.fromkeys([f["name"] for f in frames] + identifiers + words))[:20]
        yield self._step(
            "parse_bug_report",
            "Pulled stack frames, code identifiers and keywords out of the report so they can be matched "
            "against Function nodes.",
            f"{len(frames)} stack frame(s), identifiers: {', '.join(identifiers[:8]) or 'none'}; "
            f"keywords: {', '.join(words[:8]) or 'none'}",
            queries=[],
            data={"frames": frames, "identifiers": identifiers, "keywords": words},
        )

        async for ev in self._locate(frames, identifiers, words):
            yield ev
        if not self.candidates:
            n_fn = self.db.ro(self.graph, "MATCH (f:Function) RETURN count(f) AS n", {})[0]["n"]
            hint = ("The graph has no functions for this repository (unsupported language, or no functions found)."
                    if n_fn == 0 else
                    "Nothing in the code graph matched the report. Mention a function, file, UI element or error "
                    "message, or paste a stack trace.")
            yield self._step("no_match", hint, f"0 candidate functions ({n_fn} functions in the graph)", queries=[])
            async for ev in self._finish(started):
                yield ev
            return

        async for ev in self._expand():
            yield ev
        async for ev in self._gather_evidence():
            yield ev
        yield self._rank()

        if self.use_llm and self.llm.enabled:
            async for ev in self._llm_investigate():
                yield ev
        if not self.verdict:
            self._graph_verdict()
            yield self._step(
                "verdict", self.verdict["explanation"],
                f"Root cause: {self.verdict['name']} (confidence {self.verdict['confidence']:.0%})",
                queries=[], data=self.verdict,
            )

        async for ev in self._write_fix():
            yield ev
        async for ev in self._finish(started):
            yield ev

    # ------------------------------------------------------------ 1. parse

    def _parse_report(self) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        frames: List[Dict[str, Any]] = []
        for m in PY_FRAME.finditer(self.text):
            frames.append({"file": m.group(1), "line": int(m.group(2)), "name": m.group(3)})
        for m in JS_FRAME.finditer(self.text):
            frames.append({"file": m.group(2), "line": int(m.group(3)), "name": m.group(1).split(".")[-1]})
        for m in GO_JAVA_FRAME.finditer(self.text):
            frames.append({"file": m.group(2), "line": int(m.group(3)), "name": m.group(1)})
        frames = [f for f in frames if not re.search(r"site-packages|node_modules|/usr/lib|<frozen", f["file"])]

        identifiers: List[str] = []
        for m in IDENT.finditer(self.text):
            tok = m.group(1)
            looks_like_code = ("_" in tok or "." in tok or re.search(r"[a-z][A-Z]", tok) or tok.endswith("()"))
            if looks_like_code and not re.match(r"^[\w.]+\.(py|js|ts|tsx|go|java)$", tok):
                identifiers.append(tok)
        words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", self.text)]
        words = [w for w in words if w not in STOPWORDS]
        return frames, list(dict.fromkeys(identifiers))[:15], list(dict.fromkeys(words))[:15]

    # ----------------------------------------------------------- 2. locate

    def _add_candidate(self, row: Dict[str, Any], relevance: float, why: str) -> None:
        c = self.candidates.get(row["id"])
        if c is None:
            self.candidates[row["id"]] = {
                "id": row["id"], "name": row["name"], "file_path": row["file_path"],
                "relevance": relevance, "why": [why],
            }
        else:
            if relevance > c["relevance"]:
                c["relevance"] = relevance
            if why not in c["why"]:
                c["why"].append(why)

    async def _locate(self, frames, identifiers, words) -> AsyncIterator[Dict[str, Any]]:
        if frames:
            found = []
            depth = len(frames)
            for i, f in enumerate(frames):
                suffix = f["file"].lstrip("./")
                # Match on the trailing path components so absolute paths still match.
                suffix = "/".join(suffix.split("/")[-3:])
                rows = self.db.ro(self.graph, Q.SEARCH_STACK_FRAME, {"name": f["name"], "file_suffix": suffix, "line": f["line"]})
                if not rows:
                    rows = self.db.ro(self.graph, Q.SEARCH_STACK_FRAME, {"name": f["name"], "file_suffix": suffix.split("/")[-1], "line": -1})
                for r in rows[:1]:
                    # Deeper frames sit closer to the failure.
                    self._add_candidate(r, 0.85 + 0.15 * (i + 1) / depth, f"stack frame {f['file']}:{f['line']}")
                    found.append(r)
                    self.visited.add(r["id"])
            yield self._step(
                "match_stack_frames",
                "Matched each traceback frame to the Function whose file path ends with the frame's file and whose "
                "line span contains the frame's line.",
                f"{len(found)}/{len(frames)} frames matched: {', '.join(r['name'] for r in found) or 'none'}",
                queries=[compact(Q.SEARCH_STACK_FRAME)], data=found,
            )

        if identifiers:
            names = list(dict.fromkeys(identifiers + [i.split(".")[-1] for i in identifiers]))
            rows = self.db.ro(self.graph, Q.SEARCH_EXACT_NAME, {"names": names, "limit": 15})
            for r in rows:
                self._add_candidate(r, 0.9, "named in the report")
                self.visited.add(r["id"])
            yield self._step(
                "match_identifiers",
                "Looked up identifiers from the report as exact function names.",
                f"{len(rows)} exact match(es): {', '.join(r['name'] for r in rows[:6]) or 'none'}",
                queries=[compact(Q.SEARCH_EXACT_NAME)], data=rows,
            )

        if words or identifiers:
            query = " ".join(words + [w for i in identifiers for w in re.split(r"[._]", i)])
            res = self.tools.search_code(query, limit=8)
            top = max((r["score"] for r in res.data), default=1.0) or 1.0
            for r in res.data:
                self._add_candidate(r, 0.35 + 0.35 * (r["score"] / top), "full-text match")
            yield self._step(
                "search_code",
                "Full-text search over split identifiers, file-path words and docstrings, for functions the report "
                "describes without naming.", res.summary, tool=res, data=res.data,
            )

        ranked = sorted(self.candidates.values(), key=lambda c: c["relevance"], reverse=True)
        self.entry_ids = [c["id"] for c in ranked[:3]]

    # ----------------------------------------------------------- 3. expand

    async def _expand(self) -> AsyncIterator[Dict[str, Any]]:
        for fid in self.entry_ids:
            base = self.candidates[fid]
            callees = self.tools.get_callees(fid, depth=1)
            for r in (callees.data or {}).get("callees", []):
                self._add_candidate(r, base["relevance"] * 0.6, f"called by {base['name']}")
            yield self._step(
                "get_callees",
                f"Followed CALLS edges out of {base['name']}: a bug often lives in something the entry point relies on.",
                callees.summary, tool=callees, data=(callees.data or {}).get("callees"),
            )
            callers = self.tools.get_callers(fid, depth=2)
            for r in (callers.data or {}).get("callers", []):
                self._add_candidate(r, base["relevance"] * (0.5 if r["distance"] == 1 else 0.3),
                                    f"calls {base['name']} ({r['distance']} hop{'s' if r['distance'] > 1 else ''})")
            yield self._step(
                "get_callers",
                f"Followed CALLS edges into {base['name']} (up to 2 hops) to see who passes it its inputs.",
                callers.summary, tool=callers, data=(callers.data or {}).get("callers"),
            )

    # --------------------------------------------------------- 4. evidence

    async def _gather_evidence(self) -> AsyncIterator[Dict[str, Any]]:
        ids = list(self.candidates)[:40]
        for fid in list(self.candidates)[40:]:
            del self.candidates[fid]

        tests = self.tools.get_tests(ids)
        for row in tests.data:
            c = self.candidates[row["id"]]
            c["direct_tests"] = [t["name"] for t in row["direct_tests"]]
            c["indirect_tests"] = [t["name"] for t in row["indirect_tests"]]
        yield self._step("get_tests", "Checked which candidates are covered by Test nodes, directly or through a "
                         "function that calls them.", tests.summary, tool=tests)

        changes = self.tools.recent_changes(ids)
        for row in changes.data["changes"]:
            self.candidates[row["id"]]["commits"] = row["commits"]
        for row in changes.data["recency"]:
            c = self.candidates[row["id"]]
            c["touches"] = row["touches"]
            if row["last_touch"] and row["newest"] and row["oldest"] is not None:
                span = max(row["newest"] - row["oldest"], 1)
                c["recent_change"] = round((row["last_touch"] - row["oldest"]) / span, 3) ** 2
            else:
                c["recent_change"] = 0.0
        yield self._step("recent_changes", "Looked up commits whose diffs touched each candidate, and how recent the "
                         "last change is relative to the repo's history.", changes.summary, tool=changes)

        feats = self.db.ro(self.graph, Q.FEATURES_FOR, {"ids": ids})
        for row in feats:
            c = self.candidates[row["id"]]
            c.update({"complexity_raw": row["complexity"], "fan_in": row["fan_in"], "fan_out": row["fan_out"],
                      "start_line": row["start_line"]})
        pr = self.db.ro(self.graph, Q.PAGERANK_FOR, {"ids": ids}) if self.tools.pagerank_available() else []
        for row in pr:
            self.candidates[row["id"]]["centrality"] = round(row["centrality"] or 0.0, 3)
        top_central = sorted(self.candidates.values(), key=lambda c: c.get("centrality", 0), reverse=True)[:3]
        yield self._step(
            "graph_algorithms",
            "Ran PageRank over the CALLS graph to see how central each candidate is, and read fan-in/fan-out and "
            "cyclomatic complexity from the graph.",
            "Most central candidates: " + ", ".join(f"{c['name']} ({c.get('centrality', 0):.2f})" for c in top_central),
            queries=[compact(Q.PAGERANK_FOR), compact(Q.FEATURES_FOR)],
        )

        memory = self.tools.recall_past_investigations(ids, text=self.text, exclude=self.id)
        for row in memory.data["by_function"]:
            c = self.candidates.get(row["fn_id"])
            if not c:
                continue
            if row["relation"] == "CONFIRMED":
                val = 1.0 if row["same_function"] else 0.4
            else:
                # An unconfirmed suspicion is only an earlier guess. Scoring it would let a wrong
                # guess win the next run, be saved as a suspect again, and keep winning.
                val = 0.0
                if "memory_note" not in c and row["same_function"]:
                    c["memory_note"] = f"unconfirmed suspect in \"{row['bug_title']}\" (not scored)"
            if val > c.get("memory", 0):
                c["memory"] = val
                c["memory_note"] = f"confirmed root cause in \"{row['bug_title']}\"" + \
                                   ("" if row["same_function"] else f" (neighbour {row['past_fn']})")
        for row in memory.data["by_text"]:
            for fid in row["confirmed_ids"]:
                c = self.candidates.get(fid)
                if c and c.get("memory", 0) < 0.8:
                    c["memory"] = 0.8
                    c["memory_note"] = f"confirmed root cause of similar bug \"{row['bug_title']}\""
        yield self._step("recall_past_investigations", "Checked agent memory: earlier investigations stored in the "
                         "graph that suspected or confirmed these functions or their neighbours.",
                         memory.summary, tool=memory, data=memory.data)

    # ------------------------------------------------------------- 5. rank

    def _rank(self) -> Dict[str, Any]:
        for c in self.candidates.values():
            direct, indirect = c.get("direct_tests", []), c.get("indirect_tests", [])
            breakdown = {
                "relevance": round(c["relevance"], 3),
                "recent_change": round(c.get("recent_change", 0.0), 3),
                "untested": 0.0 if direct else (0.5 if indirect else 1.0),
                "memory": round(c.get("memory", 0.0), 3),
                "complexity": round(min(c.get("complexity_raw", 1), 15) / 15, 3),
                "centrality": round(c.get("centrality", 0.0), 3),
            }
            c["breakdown"] = breakdown
            c["score"] = round(sum(WEIGHTS[k] * v for k, v in breakdown.items()), 4)

        ranked = sorted(self.candidates.values(), key=lambda c: c["score"], reverse=True)
        self.suspects = []
        for rank, c in enumerate(ranked[:10], start=1):
            commits = c.get("commits", [])
            reasons = list(c["why"][:2])
            if commits:
                reasons.append(f"last changed by {commits[0]['author']}: \"{commits[0]['message'][:60]}\"")
            if not c.get("direct_tests"):
                reasons.append("no direct tests" if not c.get("indirect_tests") else "only tested indirectly")
            if c.get("memory_note"):
                reasons.append(c["memory_note"])
            self.suspects.append({
                "rank": rank,
                "hash": c["id"],
                "id": c["id"],
                "name": c["name"],
                "file_path": c["file_path"],
                "start_line": c.get("start_line"),
                "suspicion_score": c["score"],
                "breakdown": c["breakdown"],
                "caller_count": c.get("fan_in", 0),
                "test_count": len(c.get("direct_tests", [])),
                "recent_commits": c.get("touches", 0),
                "tests": c.get("direct_tests", []) + c.get("indirect_tests", []),
                "commits": commits[:3],
                "reasoning": "; ".join(reasons),
            })
        weights = " + ".join(f"{w:.2f}*{k}" for k, w in WEIGHTS.items())
        return self._step(
            "rank_suspects",
            f"Scored every candidate: score = {weights}. Each factor comes from the graph (search match, TOUCHED "
            f"commits, TESTS edges, past Investigations, complexity, PageRank).",
            "Top suspects: " + ", ".join(f"{s['name']} ({s['suspicion_score']:.2f})" for s in self.suspects[:3]),
            queries=[], data=self.suspects,
        )

    # ----------------------------------------------------------- 6. LLM loop

    async def _llm_investigate(self) -> AsyncIterator[Dict[str, Any]]:
        model = await self.llm.pick_model(settings.OLLAMA_AGENT_MODEL)
        if model is None:
            yield self._step("llm_unavailable", f"No tool-capable model on {settings.OLLAMA_HOST} (wanted "
                             f"'{settings.OLLAMA_AGENT_MODEL}'); continuing with the graph-only verdict.",
                             "LLM skipped", queries=[])
            return
        self.agent_model = model

        top = self.suspects[:5]
        code_blocks = []
        for s in top[:2]:
            fn = self.tools.get_function(s["id"]).data or {}
            code_blocks.append(f"### {s['name']} ({s['file_path']}:{fn.get('start_line')})\n```\n{fn.get('code', '')[:1500]}\n```")
        table = "\n".join(f"{s['rank']}. {s['name']} [{s['file_path']}] score={s['suspicion_score']:.2f} — {s['reasoning']}"
                          for s in top)
        system = (
            "You are Codebase Detective, a debugging agent. The codebase is stored as a graph (functions, CALLS, "
            "tests, commits, authors). Use the tools to verify which function contains the root cause. Prefer "
            "reading code (get_function) and following calls. Be brief. When you are confident, reply with ONLY a "
            'JSON object: {"root_cause": "<function name>", "explanation": "<2-4 sentences citing graph evidence>", '
            '"confidence": <0..1>}.'
        )
        user = (f"Bug report:\n{self.title}\n{self.description}\n\nGraph-ranked suspects:\n{table}\n\n"
                f"Source of the top suspects:\n" + "\n\n".join(code_blocks))
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        tools = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"],
            "parameters": {"type": "object", "properties": t["parameters"], "required": t["required"]},
        }} for t in TOOL_SPECS]

        yield {"type": "status", "status": "running", "message": f"LLM agent ({model}) is investigating"}
        use_tools = True
        calls_made = 0
        nudged = False
        final_text = ""
        for _ in range(settings.MAX_AGENT_TOOL_CALLS + 3):
            try:
                reply = await self.llm.chat(model, messages, tools=tools if use_tools else None)
            except ToolsUnsupported:
                use_tools = False
                yield self._step("llm_no_tools", f"{model} cannot call tools; asking it to judge the evidence directly.",
                                 "Falling back to single-shot reasoning", queries=[], actor="llm")
                continue
            except LLMUnavailable as e:
                yield self._step("llm_unavailable", f"LLM call failed ({str(e)[:120]}); using the graph-only verdict.",
                                 "LLM skipped", queries=[])
                return
            messages.append({"role": "assistant", "content": reply["content"], "tool_calls": [
                {"function": {"name": c["name"], "arguments": c["arguments"]}} for c in reply["tool_calls"]]})
            if not reply["tool_calls"] and use_tools:
                reply["tool_calls"] = self._text_tool_calls(reply["content"])
            if not reply["tool_calls"]:
                unusable = not reply["content"].strip() or self._parse_verdict(reply["content"]) is None
                if unusable and not nudged:
                    # Small models sometimes emit a malformed tool call that arrives empty.
                    nudged = True
                    messages.pop()
                    messages.append({"role": "user", "content": "Either call exactly one tool (for example get_function "
                                     f"with function=\"{top[0]['name']}\"), or reply with ONLY the JSON verdict "
                                     '{"root_cause": "<function name>", "explanation": "...", "confidence": 0.0-1.0}.'})
                    continue
                final_text = reply["content"]
                break
            for call in reply["tool_calls"]:
                calls_made += 1
                if calls_made > settings.MAX_AGENT_TOOL_CALLS:
                    break
                res = run_tool(self.tools, call["name"], call["arguments"], exclude=self.id)
                for fid in res.visited:
                    if fid in self.candidates:
                        self.candidates[fid].setdefault("why", []).append(f"examined by LLM via {res.tool}")
                yield self._step(f"llm_{res.tool}", f"The LLM chose {res.tool}({json.dumps(call['arguments'])[:120]}).",
                                 res.summary, tool=res, actor="llm")
                payload = res.data if res.tool != "get_function" else {k: v for k, v in (res.data or {}).items()}
                messages.append({"role": "tool", "tool_name": res.tool,
                                 "content": json.dumps(payload, default=str)[:3500] if res.error is None else res.summary})
            if calls_made >= settings.MAX_AGENT_TOOL_CALLS:
                messages.append({"role": "user", "content": "Tool budget used up. Reply now with the JSON verdict only."})
                use_tools = False

        verdict = self._parse_verdict(final_text)
        if verdict:
            self.verdict = verdict
            self.mode = "graph+llm"
            yield self._step("verdict", verdict["explanation"],
                             f"Root cause: {verdict['name']} (confidence {verdict['confidence']:.0%})",
                             queries=[], actor="llm", data=verdict)
        else:
            yield self._step("llm_unparsed", "The LLM did not return a usable verdict; using the graph ranking.",
                             (final_text or "empty reply")[:200], queries=[], actor="llm")

    @staticmethod
    def _text_tool_calls(text: str) -> List[Dict[str, Any]]:
        """Small models sometimes write a tool call as JSON text instead of using the tool-call format."""
        names = {t["name"] for t in TOOL_SPECS}
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
        if isinstance(obj, dict) and obj.get("name") in names:
            args = obj.get("arguments") or obj.get("parameters") or {}
            return [{"name": obj["name"], "arguments": args if isinstance(args, dict) else {}}]
        return []

    def _parse_verdict(self, text: str) -> Optional[Dict[str, Any]]:
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        name = str(obj.get("root_cause", "")).strip()
        prefer = {s["id"] for s in self.suspects}
        fn = self.tools.resolve(name, prefer=prefer) if name else None
        if not fn:
            return None
        try:
            conf = float(obj.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        return {"id": fn["id"], "name": fn["name"], "file_path": fn["file_path"],
                "explanation": str(obj.get("explanation", ""))[:1200], "confidence": max(0.0, min(conf, 1.0)),
                "source": "llm"}

    def _graph_verdict(self) -> None:
        top = self.suspects[0]
        second = self.suspects[1]["suspicion_score"] if len(self.suspects) > 1 else 0.0
        margin = top["suspicion_score"] - second
        confidence = round(min(0.95, 0.45 + top["suspicion_score"] * 0.4 + margin * 1.5), 2)
        parts = [f"{top['name']} in {top['file_path']} scores highest ({top['suspicion_score']:.2f})."]
        b = top["breakdown"]
        if b["relevance"] >= 0.85:
            parts.append("It is named in the report or sits on the stack trace.")
        if b["recent_change"] >= 0.5 and top["commits"]:
            c = top["commits"][0]
            parts.append(f"It was recently changed by {c['author']} (\"{c['message'][:70]}\").")
        if b["untested"] >= 1:
            parts.append("No test exercises it.")
        elif b["untested"] >= 0.5:
            parts.append("It is only tested indirectly.")
        if b["memory"] >= 0.5:
            parts.append("Agent memory links it to an earlier bug.")
        self.verdict = {"id": top["id"], "name": top["name"], "file_path": top["file_path"],
                        "explanation": " ".join(parts), "confidence": confidence, "source": "graph"}

    # ------------------------------------------------------------- 7. fix

    async def _write_fix(self) -> AsyncIterator[Dict[str, Any]]:
        target = self.tools.get_function(self.verdict["id"])
        fn = target.data or {}
        blast = self.tools.blast_radius(self.verdict["id"])
        experts = self.tools.find_experts(self.verdict["id"])
        yield self._step("blast_radius", "Measured what a change to the root cause would affect, so the fix can be "
                         "reviewed with the right scope.", blast.summary, tool=blast, data=blast.data)
        yield self._step("find_experts", "Found who knows this code (authors of commits that touched it) to suggest "
                         "reviewers.", experts.summary, tool=experts, data=experts.data)

        tests = [t for s in self.suspects if s["id"] == self.verdict["id"] for t in s["tests"]]
        self.fix = {
            "target": {"id": fn.get("id"), "name": fn.get("name"), "file_path": fn.get("file_path"),
                       "start_line": fn.get("start_line"), "end_line": fn.get("end_line"), "code": fn.get("code")},
            "diff": "",
            "explanation": self.verdict["explanation"],
            "confidence": self.verdict["confidence"],
            "files_changed": [fn.get("file_path")] if fn.get("file_path") else [],
            "reviewers": experts.data or [],
            "blast_radius": blast.data,
            "tests": tests,
        }
        fix_model = None
        if self.use_llm and self.llm.enabled:
            fix_model = await self.llm.pick_model(settings.OLLAMA_MODEL, fallbacks=("qwen2.5-coder", "deepseek-coder", "codellama")) \
                or getattr(self, "agent_model", None)
        if not fix_model:
            yield self._step("generate_fix", "No code model available, so no patch was written. The root cause, its "
                             "source and the reviewers are in the Fix tab.", "Patch skipped", queries=[])
            return

        callees = self.tools.get_callees(self.verdict["id"], depth=1)
        context = []
        for c in (callees.data or {}).get("callees", [])[:4]:
            code = (self.tools.get_function(c["id"]).data or {}).get("code", "")
            context.append(f"# {c['file_path']}\n{code[:900]}")
        prompt = (
            f"Bug report:\n{self.title}\n{self.description}\n\nRoot cause (from graph investigation): "
            f"{fn.get('name')} in {fn.get('file_path')} lines {fn.get('start_line')}-{fn.get('end_line')}.\n"
            f"Why: {self.verdict['explanation']}\n\nCurrent code of {fn.get('file_path')} "
            f"(starting at line {fn.get('start_line')}):\n```\n{fn.get('code')}\n```\n\n"
            f"Functions it calls:\n```\n" + "\n\n".join(context) + "\n```\n\n"
            "First write one line starting with 'Cause:' naming the exact defect (which value is wrong and where "
            "it comes from; check what the called functions can return). Then output the COMPLETE corrected "
            f"version of the code shown above for {fn.get('name')} in a single ```code block```: start with exactly "
            "the same first line, keep its indentation, and change as little as possible. If you cannot find a real "
            "defect in this code, say so on the Cause line and return it unchanged. Finish with one line starting "
            "with 'Test:' describing the regression test to add."
        )
        yield {"type": "status", "status": "running", "message": f"Writing a patch with {fix_model}"}
        try:
            reply = await self.llm.chat(fix_model, [
                {"role": "system", "content": "You are a senior engineer fixing a production bug. Be precise and minimal."},
                {"role": "user", "content": prompt},
            ])
        except LLMUnavailable as e:
            yield self._step("generate_fix", f"Patch generation failed: {str(e)[:150]}", "Patch skipped", queries=[])
            return
        diff, test_note = self._build_diff(reply["content"], fn)
        cause = re.search(r"^\s*\**Cause:?\**:?\s*(.+)$", reply["content"], re.M)
        self.fix["diff"] = diff
        self.fix["test_suggestion"] = test_note
        self.mode = "graph+llm"
        self.fix["cause"] = cause.group(1).strip() if cause else ""
        yield self._step("generate_fix", f"{fix_model} wrote a patch from the actual source of the root "
                         "cause and the functions it calls (both read from the graph).",
                         f"Patch for {fn.get('file_path')} ({diff.count(chr(10)) + 1 if diff else 0} lines)",
                         queries=[compact(Q.GET_FUNCTION), compact(Q.GET_CALLEES)], actor="llm")

    @staticmethod
    def _build_diff(text: str, fn: Dict[str, Any]) -> Tuple[str, str]:
        """Diff the model's corrected function against the real source, so the
        patch always has correct context and line numbers."""
        test_note = ""
        m = re.search(r"^\W*Test:\W*(.+)$", text, re.M)
        if m:
            test_note = m.group(1).strip()
        blocks = re.findall(r"```[\w+-]*\n(.*?)```", text, re.S)
        if not blocks:
            return "", test_note
        new_code = max(blocks, key=len).rstrip("\n")
        old_code = (fn.get("code") or "").rstrip("\n")
        old_lines, new_lines = old_code.splitlines(), new_code.splitlines()
        if old_lines and new_lines:
            # Models often wrap an anonymous callback in its call, e.g. `form.on("x", (e) => {` ... `});`.
            head = old_lines[0].strip()
            if new_lines[0].strip() != head and head in new_lines[0]:
                new_lines[0] = old_lines[0]
                if new_lines[-1].strip() in ("});", "})") and old_lines[-1].strip() == "}":
                    new_lines[-1] = new_lines[-1].replace("})", "}").rstrip(";")
                    if old_lines[-1].rstrip().endswith("};"):
                        new_lines[-1] += ";"
        # Ignore whitespace-only differences and blank lines.
        if [ln.strip() for ln in old_lines if ln.strip()] == [ln.strip() for ln in new_lines if ln.strip()]:
            return "", test_note
        # Models often drop the method's leading indentation; restore it.
        indent = re.match(r"\s*", old_lines[1] if len(old_lines) > 1 else "").group(0) if old_lines else ""
        if old_lines and new_lines and not new_lines[0].startswith((" ", "\t")) and len(old_lines) > 1 \
                and old_lines[1].startswith(indent) and indent and not new_lines[1:2] == old_lines[1:2]:
            body_indent = re.match(r"\s*", new_lines[1]).group(0) if len(new_lines) > 1 else ""
            if len(body_indent) < len(indent):
                pad = indent[:len(indent) - len(body_indent)]
                new_lines = [new_lines[0]] + [pad + ln if ln.strip() else ln for ln in new_lines[1:]]
        if old_lines == new_lines:
            return "", test_note
        path = fn.get("file_path", "")
        start = int(fn.get("start_line") or 1)
        diff = list(difflib.unified_diff(old_lines, new_lines, f"a/{path}", f"b/{path}", lineterm="", n=3))
        out = []
        for line in diff:
            h = re.match(r"^@@ -(\d+)(,\d+)? \+(\d+)(,\d+)? @@", line)
            if h:  # shift hunk headers from function-relative to file line numbers
                line = f"@@ -{int(h.group(1)) + start - 1}{h.group(2) or ''} +{int(h.group(3)) + start - 1}{h.group(4) or ''} @@"
            out.append(line)
        return "\n".join(out), test_note

    # ------------------------------------------------------------ 8. memory

    def _save_bug_report(self, status: str) -> None:
        self.db.query(self.graph, Q.SAVE_BUG_REPORT, {
            "id": self.bug_id, "title": self.title, "description": self.description,
            "created_at": self.created_at, "status": status,
        })

    async def _finish(self, started: float) -> AsyncIterator[Dict[str, Any]]:
        status = "completed"
        summary = (f"{self.verdict['name']}: {self.verdict['explanation'][:300]}" if self.verdict
                   else "No matching code found")
        if self.verdict:
            self.db.query(self.graph, Q.LINK_MENTIONS, {"bug_id": self.bug_id, "ids": self.entry_ids})
        self._save_bug_report(status)
        result = self.result(status)
        self.db.query(self.graph, Q.SAVE_INVESTIGATION, {
            "bug_id": self.bug_id, "id": self.id, "title": self.title, "status": status, "summary": summary,
            "created_at": self.created_at, "completed_at": result["completed_at"], "keywords": self.keywords,
            "keywords_text": " ".join(self.keywords), "mode": self.mode,
            "payload": json.dumps(result, default=str),
        })
        if self.suspects:
            self.db.query(self.graph, Q.LINK_SUSPECTS, {"id": self.id, "rows": [
                {"fn_id": s["id"], "rank": s["rank"], "score": s["suspicion_score"]} for s in self.suspects[:5]]})
        yield self._step("save_memory", "Saved this investigation to the graph: (BugReport)-[:INVESTIGATED_IN]->"
                         "(Investigation)-[:SUSPECTS]->(Function). Future investigations recall it.",
                         f"Investigation stored; took {time.time() - started:.1f}s",
                         queries=[compact(Q.SAVE_INVESTIGATION), compact(Q.LINK_SUSPECTS)])
        result = self.result(status)
        self.db.query(self.graph, "MATCH (i:Investigation {id: $id}) SET i.payload = $payload",
                      {"id": self.id, "payload": json.dumps(result, default=str)})
        yield {"type": "result", "result": result}

    def result(self, status: str) -> Dict[str, Any]:
        return {
            "id": self.id,
            "bug_report_id": self.bug_id,
            "repository_id": self.repo_id,
            "title": self.title,
            "description": self.description,
            "status": status,
            "mode": self.mode,
            "steps": self.steps,
            "suspects": self.suspects,
            "verdict": self.verdict,
            "fix": self.fix,
            "fix_suggestion": self.fix.get("diff") if self.fix else None,
            "evidence_ids": sorted(self.visited | set(self.candidates)),
            "created_at": self.created_at,
            "completed_at": now_iso(),
        }


def new_id() -> str:
    return str(uuid.uuid4())
