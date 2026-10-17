"""Closes the loop after an investigation: prove the fix before anyone opens a pull request.

1. Select the tests to run from the graph (test impact analysis): every Test whose
   TESTS edge reaches the root cause directly or through up to 3 CALLS hops.
2. Write a regression test that reproduces the bug (a model drafts it from the bug
   report and the graph context, or a person or MCP client supplies it).
3. In a sandbox copy of the repository, run the regression test and the selected
   tests on the original code: the regression test should fail.
4. Splice the corrected function in and run the same tests again: the regression
   test should pass and every selected test that passed before should still pass.
5. Write the outcome to the graph: (Investigation)-[:PROPOSED]->(Fix)-[:VERIFIED_BY]->
   (TestRun)-[:RAN]->(Test). A fix proven this way confirms the root cause in memory.
6. Draft the pull request from the graph: the issue it fixes, the decision it restores,
   the tests that prove it, and reviewers from CODEOWNERS and authorship.
"""
import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, AsyncIterator, Dict, List, Optional

import structlog

from app.core.config import settings
from app.core.falkordb import get_falkordb
from app.services import cypher_queries as Q
from app.services import sandbox
from app.services.graph_tools import GraphTools, compact
from app.services.llm import LLMUnavailable, get_llm
from app.services.test_writer import CODE_MODELS, LANGUAGE_BY_EXT

logger = structlog.get_logger()

STATUS_TEXT = {
    "verified": "Verified: the regression test failed on the original code and passes with the fix, and no "
                "selected test broke.",
    "regresses": "The fix breaks tests that passed before.",
    "not_fixed": "The regression test reproduces the bug, but still fails with the fix.",
    "not_reproduced": "The regression test already passes on the original code, so it does not reproduce the bug "
                      "and the fix is unproven.",
    "no_regressions": "No regression test was run; the selected tests still pass with the fix, but nothing shows it "
                      "fixes the bug.",
    "error": "The tests could not be run.",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_python(path: str) -> bool:
    return LANGUAGE_BY_EXT.get(PurePosixPath(path or "").suffix.lower()) == "python"


def regression_test_path(fn_path: str, impacted: List[Dict[str, Any]], investigation_id: str) -> str:
    folder = PurePosixPath(impacted[0]["file_path"]).parent if impacted else PurePosixPath("tests")
    return str(folder / f"test_regression_{investigation_id.replace('-', '')[:8]}.py")


def impacted_tests(graph_name: str, fn_id: str) -> Dict[str, Any]:
    db = get_falkordb()
    rows = db.ro(graph_name, Q.TEST_IMPACT, {"id": fn_id})
    total = db.ro(graph_name, Q.COUNT_TESTS, {})[0]["n"]
    return {"tests": rows, "total": total}


# ------------------------------------------------------------------ drafting

def build_regression_prompt(inv: Dict[str, Any], ctx: Dict[str, Any], test_path: str) -> str:
    verdict = inv.get("verdict") or {}
    examples = "\n\n".join(f"# {t['file_path']}\n{(t.get('code') or '')[:1200]}" for t in ctx["example_tests"])
    callees = "\n\n".join(f"# {c['file_path']} ({c['name']})\n{(c.get('code') or '')[:900]}" for c in ctx["callees"])
    callers = "\n\n".join(f"# {c['file_path']} ({c['name']})\n{(c.get('code') or '')[:900]}" for c in ctx["callers"])
    decisions = "\n".join(f"- {d['text']}" for d in ((inv.get("fix") or {}).get("history") or {}).get("decisions", []))
    return (
        f"Bug report:\n{inv.get('title', '')}\n{inv.get('description', '')}\n\n"
        f"The investigation found the root cause in `{ctx['name']}` ({ctx['file_path']}): "
        f"{verdict.get('explanation', '')}\n\n"
        f"Code of `{ctx['name']}`:\n```\n{ctx['code']}\n```\n\n"
        + (f"The rest of `{ctx['file_path']}` outside its functions:\n```\n{ctx['module_code']}\n```\n\n"
           if ctx.get("module_code") else "")
        + (f"Its callers:\n```\n{callers}\n```\n\n" if callers else "")
        + (f"What it calls:\n```\n{callees}\n```\n\n" if callees else "")
        + (f"Existing tests in this repository (follow their imports and setup):\n```\n{examples}\n```\n\n"
           if examples else "")
        + (f"Team decisions about this code (from the company knowledge graph); they define the correct "
           f"behaviour:\n{decisions}\n\n" if decisions else "")
        + f"Write a pytest regression test file that will be saved as `{test_path}`. It must FAIL on the code "
        "above because of the reported bug, and PASS once the bug is fixed. The error in the bug report IS the bug: "
        "never expect it (no pytest.raises for it, no assertion on its message). Call the code the way the bug "
        "report describes, and assert the correct behaviour (for example that a clear, specific exception is raised "
        "or an error result is returned), not merely that no exception escapes. If the code or a team decision "
        "already defines an exception for this case, expect that exception with pytest.raises. Import every name "
        "from its real "
        "module and use only names and data that exist in the code shown. Write one or two test functions. "
        "Output a single ```python code block``` with the complete file."
    )


async def draft(graph_name: str, inv: Dict[str, Any]) -> Dict[str, Any]:
    """Everything the verify step needs, ready for a person to review or edit: the corrected function,
    a regression test, and the tests the graph selected."""
    verdict = inv.get("verdict") or {}
    fix = inv.get("fix") or {}
    target = fix.get("target") or {}
    if not verdict.get("id"):
        raise ValueError("This investigation has no root cause to fix.")
    tools = GraphTools(graph_name)
    impact = impacted_tests(graph_name, verdict["id"])
    test_path = regression_test_path(target.get("file_path", ""), impact["tests"], inv["id"])
    out: Dict[str, Any] = {
        "function": {k: target.get(k) for k in ("id", "name", "file_path", "start_line", "end_line")},
        "original_code": target.get("code") or "",
        "patched_code": fix.get("patched_code") or "",
        "diff": fix.get("diff") or "",
        "test_path": test_path,
        "test_code": "",
        "test_model": None,
        "impacted_tests": impact["tests"],
        "total_tests": impact["total"],
        "cypher": compact(Q.TEST_IMPACT),
        "supported": _is_python(target.get("file_path", "")),
        "blocked": None,
        "note": None,
    }
    if not out["supported"]:
        out["note"] = "The sandbox runs Python repositories with pytest; this root cause is in another language."
        return out
    ctx_res = tools.test_context(verdict["id"])
    if ctx_res.error:
        return out
    llm = get_llm()
    model = await llm.pick_model(settings.OLLAMA_MODEL, fallbacks=CODE_MODELS) if llm.enabled else None
    if not model:
        out["note"] = ("This server runs without a model, so write the regression test yourself (or paste one from "
                       "an MCP client) to prove the fix.")
        return out
    try:
        reply = await llm.chat(model, [
            {"role": "system", "content": "You write minimal pytest regression tests that reproduce reported bugs."},
            {"role": "user", "content": build_regression_prompt(inv, ctx_res.data, test_path)},
        ])
    except LLMUnavailable as e:
        out["note"] = f"The model could not be reached: {str(e)[:150]}"
        return out
    blocks = re.findall(r"```[a-zA-Z0-9_+-]*\n(.*?)```", reply["content"], re.S)
    out["test_code"] = max(blocks, key=len).strip() + "\n" if blocks else ""
    out["test_model"] = model
    return out


# ------------------------------------------------------------- verification

class Verification:
    def __init__(self, repo: Dict[str, Any], inv: Dict[str, Any], patched_code: str = "", diff: str = "",
                 test_code: str = "", test_path: str = ""):
        self.repo = repo
        self.graph = repo["graph_name"]
        self.inv = inv
        self.fix_in = inv.get("fix") or {}
        self.target = self.fix_in.get("target") or {}
        self.verdict = inv.get("verdict") or {}
        self.patched_code = patched_code
        self.diff_in = diff
        self.test_code = test_code
        self.test_path = test_path
        self.id = str(uuid.uuid4())
        self.db = get_falkordb()
        self.steps: List[Dict[str, Any]] = []
        self.result: Dict[str, Any] = {}

    def _step(self, action: str, reasoning: str, summary: str, queries: Optional[List[str]] = None,
              data: Any = None, actor: str = "sandbox") -> Dict[str, Any]:
        step = {"step": len(self.steps) + 1, "action": action, "actor": actor, "tool": None, "args": None,
                "query": "\n\n".join(queries or []), "result_summary": summary, "reasoning": reasoning,
                "data": data, "timestamp": now_iso()}
        self.steps.append(step)
        return {"type": "step", "step": step}

    async def run(self) -> AsyncIterator[Dict[str, Any]]:
        started = time.time()
        fn_id = self.verdict.get("id")
        path = self.target.get("file_path") or ""
        if not fn_id or not self.target.get("code"):
            yield {"type": "error", "message": "This investigation has no root cause to fix."}
            return
        if not (self.patched_code.strip() or self.diff_in.strip()):
            yield {"type": "error", "message": "No fix to verify: supply the corrected function or a diff."}
            return
        if not _is_python(path):
            yield {"type": "error", "message": "The sandbox runs Python repositories with pytest only."}
            return
        blocked = sandbox.verification_allowed(self.repo["url"])
        if blocked:
            yield {"type": "error", "message": blocked}
            return

        impact = impacted_tests(self.graph, fn_id)
        selected = impact["tests"]
        yield self._step(
            "select_tests",
            f"Test impact analysis on the graph: the tests whose TESTS edge reaches {self.target['name']} directly "
            "or through up to 3 CALLS hops are the ones this change can break. Only those are run.",
            f"{len(selected)} of {impact['total']} tests selected: "
            + (", ".join(t["name"] for t in selected[:5]) or "none reach the root cause"),
            queries=[compact(Q.TEST_IMPACT)], data=selected)

        yield {"type": "status", "status": "running", "message": "Copying the repository into a sandbox"}
        try:
            ws = await asyncio.to_thread(sandbox.prepare, self.repo["url"], self.repo.get("branch") or None)
        except sandbox.SandboxError as e:
            yield {"type": "error", "message": str(e)}
            return
        try:
            async for ev in self._run_in(ws, selected, impact["total"]):
                yield ev
        finally:
            ws.cleanup()
        self.result["duration_ms"] = int((time.time() - started) * 1000)
        yield {"type": "result", "result": self.result}

    async def _run_in(self, ws: sandbox.Workspace, selected: List[Dict[str, Any]], total: int) -> AsyncIterator[Dict[str, Any]]:
        yield self._step(
            "prepare_sandbox",
            "Copied the repository into a throwaway directory. Tests run in a child process with a scrubbed "
            "environment (no server credentials), its own HOME, CPU and file-size limits and a "
            f"{settings.VERIFY_TIMEOUT_SECONDS}s timeout.",
            f"Sandbox ready{f' at commit {ws.head[:10]}' if ws.head else ''}", data={"head": ws.head})

        node_ids = [sandbox.pytest_node_id(t["file_path"], t["name"]) for t in selected]
        if self.test_code.strip():
            self.test_path = self.test_path or regression_test_path(self.target["file_path"], selected, self.inv["id"])
            ws.write(self.test_path, self.test_code)
            node_ids.insert(0, self.test_path)
            yield self._step("write_regression_test", "Added a regression test that reproduces the reported bug. "
                             "It should fail on the original code and pass once the fix is in.",
                             f"Wrote {self.test_path}", data={"path": self.test_path})
        if not node_ids:
            yield {"type": "error", "message": "Nothing to run: no test reaches the root cause and no regression "
                                               "test was supplied."}
            return

        yield {"type": "status", "status": "running", "message": "Running the tests on the original code"}
        before = await asyncio.to_thread(sandbox.run_pytest, ws, node_ids)
        yield self._step("baseline_run", "Ran the regression test and the selected tests on the original code, to "
                         "see the bug happen and to know which tests passed before the change.",
                         self._counts(before), data=self._brief(before))

        try:
            if self.patched_code.strip():
                diff = sandbox.splice_function(ws, self.target["file_path"], self.target["code"], self.patched_code,
                                               int(self.target.get("start_line") or 1))
            else:
                diff = sandbox.apply_diff(ws, self.diff_in)
        except sandbox.SandboxError as e:
            yield {"type": "error", "message": str(e)}
            return
        try:
            repaired = self._repair_imports(ws)
            for f, text in ws.patched_files.items():
                if f.endswith(".py"):
                    sandbox.check_python(text, f)
        except sandbox.SandboxError as e:
            yield {"type": "error", "message": str(e)}
            return
        if repaired:
            diff = "".join(sandbox.file_diff(ws, f) for f in ws.patched_files)
        yield self._step(
            "apply_patch",
            f"Replaced {self.target['name']} with the corrected version, located by its line span and source from "
            "the graph." + (" The patch uses names the file never imports; the graph says where each is defined, so "
                            "the imports were added." if repaired else ""),
            f"Patched {', '.join(ws.patched_files) or self.target['file_path']}"
            + (f"; {'; '.join(r['how'] for r in repaired)}" if repaired else ""),
            queries=[compact(Q.DEFINITIONS_OF)] if repaired else [], data={"diff": diff, "imports": repaired})

        yield {"type": "status", "status": "running", "message": "Running the tests on the patched code"}
        after = await asyncio.to_thread(sandbox.run_pytest, ws, node_ids)
        yield self._step("patched_run", "Ran the same tests with the fix applied.", self._counts(after),
                         data=self._brief(after))

        judgement = self._judge(before, after)
        yield self._step("judge", STATUS_TEXT[judgement["status"]], judgement["headline"], data=judgement)

        self.result = {
            "id": self.id,
            "investigation_id": self.inv["id"],
            "status": judgement["status"],
            "headline": judgement["headline"],
            "explanation": STATUS_TEXT[judgement["status"]],
            "function": {k: self.target.get(k) for k in ("id", "name", "file_path", "start_line")},
            "diff": diff,
            "patched_code": self.patched_code,
            "test_path": self.test_path if self.test_code.strip() else "",
            "test_code": self.test_code,
            "selected_tests": selected,
            "total_tests": total,
            "comparison": judgement["comparison"],
            "before": self._brief(before),
            "after": self._brief(after),
            "base_commit": ws.head,
            "files": ws.patched_files | ({self.test_path: self.test_code} if self.test_code.strip() else {}),
            "created_at": now_iso(),
            "pull_request": None,
        }
        self.result["pr_draft"] = build_pr_draft(self.graph, self.inv, self.result)
        for ev in self._save(selected, before, after):
            yield ev
        self.result["steps"] = self.steps

    def _repair_imports(self, ws: sandbox.Workspace) -> List[Dict[str, str]]:
        path = self.target["file_path"]
        text = ws.read(path)
        sandbox.check_python(text, path)
        missing = sandbox.undefined_names(text)
        if not missing:
            return []
        rows = self.db.ro(self.graph, Q.DEFINITIONS_OF, {"names": missing})
        here = sandbox.module_for(path)
        added = []
        for name in missing:
            homes = [r["file_path"] for r in rows if r["name"] == name and r["file_path"] != path]
            if not homes:
                continue
            # Prefer a module this file already imports from.
            imported = [h for h in homes if re.search(rf"^from\s+{re.escape(sandbox.module_for(h))}\s+import", text, re.M)]
            home = (imported or sorted(homes))[0]
            module = sandbox.module_for(home)
            if module and module != here:
                added.append({"name": name, "module": module, "defined_in": home,
                              "how": sandbox.add_import(ws, path, module, name)})
                text = ws.read(path)
        return added

    @staticmethod
    def _counts(run: Dict[str, Any]) -> str:
        if run["timed_out"]:
            return f"Timed out after {settings.VERIFY_TIMEOUT_SECONDS}s"
        if run["collection_errors"]:
            return f"pytest could not collect the tests: {run['collection_errors'][0][:160]}"
        if not run["tests"]:
            return f"pytest reported no tests (exit code {run['exit_code']})"
        return (f"{run['passed']} passed, {run['failed']} failed"
                + (f", {run['error']} errors" if run["error"] else "") + f" in {run['duration_ms'] / 1000:.1f}s")

    @staticmethod
    def _brief(run: Dict[str, Any]) -> Dict[str, Any]:
        return {k: run[k] for k in ("command", "exit_code", "timed_out", "duration_ms", "tests", "passed", "failed",
                                    "error", "skipped", "collection_errors", "output")}

    def _judge(self, before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        key = lambda t: (t["file_path"], t["name"])  # noqa: E731
        b = {key(t): t for t in before["tests"]}
        a = {key(t): t for t in after["tests"]}
        regression_file = self.test_path if self.test_code.strip() else None
        rows = []
        for k in list(dict.fromkeys(list(b) + list(a))):
            rows.append({"file_path": k[0], "name": k[1], "regression": k[0] == regression_file,
                         "before": b.get(k, {}).get("outcome", "missing"), "after": a.get(k, {}).get("outcome", "missing"),
                         "before_message": b.get(k, {}).get("message", ""), "after_message": a.get(k, {}).get("message", "")})
        reg = [r for r in rows if r["regression"]]
        existing = [r for r in rows if not r["regression"]]
        broken = [r for r in existing if r["before"] == "passed" and r["after"] != "passed"]
        if before["timed_out"] or after["timed_out"] or not before["tests"] or not after["tests"]:
            status = "error"
        elif broken:
            status = "regresses"
        elif reg:
            reproduced = any(r["before"] in ("failed", "error") for r in reg)
            fixed = all(r["after"] == "passed" for r in reg)
            status = "verified" if reproduced and fixed else "not_fixed" if reproduced else "not_reproduced"
        else:
            status = "no_regressions"
        kept = sum(r["before"] == "passed" and r["after"] == "passed" for r in existing)
        parts = []
        if reg:
            parts.append(f"regression test {'/'.join(sorted({r['before'] for r in reg}))} → "
                         f"{'/'.join(sorted({r['after'] for r in reg}))}")
        if existing:
            parts.append(f"{kept}/{len(existing)} selected tests still pass")
        if broken:
            parts.append("broke " + ", ".join(r["name"] for r in broken[:3]))
        return {"status": status, "headline": "; ".join(parts) or STATUS_TEXT[status], "comparison": rows,
                "broken": [r["name"] for r in broken]}

    def _save(self, selected, before, after):
        fn_id = self.verdict["id"]
        status = self.result["status"]
        self.db.query(self.graph, Q.SAVE_FIX, {
            "investigation_id": self.inv["id"], "fn_id": fn_id, "id": self.id, "status": status,
            "summary": self.result["headline"], "created_at": self.result["created_at"],
            "payload": json.dumps(self.result, default=str)})
        ids_by_key = {(t["file_path"], t["name"]): t["id"] for t in selected}
        for phase, run in (("original", before), ("patched", after)):
            run_id = str(uuid.uuid4())
            self.db.query(self.graph, Q.SAVE_TEST_RUN, {
                "fix_id": self.id, "id": run_id, "phase": phase, "passed": run["passed"], "failed": run["failed"],
                "errors": run["error"], "duration_ms": run["duration_ms"], "at": now_iso()})
            rows = [{"test_id": ids_by_key[(t["file_path"], t["name"])], "outcome": t["outcome"]}
                    for t in run["tests"] if (t["file_path"], t["name"]) in ids_by_key]
            if rows:
                self.db.query(self.graph, Q.LINK_TEST_RUN, {"run_id": run_id, "rows": rows})
        queries = [compact(Q.SAVE_FIX), compact(Q.SAVE_TEST_RUN), compact(Q.LINK_TEST_RUN)]
        if status == "verified":
            self.db.query(self.graph, Q.CONFIRM_BY_VERIFICATION, {
                "investigation_id": self.inv["id"], "fn_id": fn_id,
                "note": "verified: a regression test failed on the original code and passed with the fix"})
            queries.append(compact(Q.CONFIRM_BY_VERIFICATION))
        yield self._step(
            "save_to_graph",
            "Saved the fix and both test runs: (Investigation)-[:PROPOSED]->(Fix)-[:VERIFIED_BY]->(TestRun)-[:RAN]->"
            "(Test)." + (" The fix is proven, so the root cause is now CONFIRMED in agent memory and future "
                         "investigations of this code start from it." if status == "verified" else ""),
            f"Fix {self.id[:8]} stored as '{status}'", queries=queries, actor="agent")
        self.db.query(self.graph, Q.UPDATE_FIX, {"id": self.id, "status": status,
                                                 "payload": json.dumps({**self.result, "steps": self.steps},
                                                                       default=str)})


# --------------------------------------------------------------- PR drafting

def _reviewers(graph_name: str, fix: Dict[str, Any]) -> List[Dict[str, Any]]:
    """CODEOWNERS team members first, then the people who know the code, with their GitHub logins."""
    owners = fix.get("owners") or {}
    ranked = [(p["name"], p.get("email"), "; ".join(p.get("reasons", [])[:2])) for p in owners.get("ask", [])]
    for r in fix.get("reviewers") or []:
        if r["name"] not in {n for n, _, _ in ranked}:
            ranked.append((r["name"], r.get("email"), "changed this code"))
    if not ranked:
        return []
    rows = get_falkordb().ro(graph_name, Q.LOGINS_FOR, {"emails": [e for _, e, _ in ranked if e],
                                                        "names": [n for n, _, _ in ranked]})
    logins = {r["email"]: r["login"] for r in rows if r["login"]} | {r["name"]: r["login"] for r in rows if r["login"]}
    return [{"name": n, "login": logins.get(e) or logins.get(n), "why": why} for n, e, why in ranked[:4]]


def build_pr_draft(graph_name: str, inv: Dict[str, Any], ver: Dict[str, Any]) -> Dict[str, Any]:
    fix = inv.get("fix") or {}
    verdict = inv.get("verdict") or {}
    history = fix.get("history") or {}
    fn = ver["function"]
    short = inv["id"].replace("-", "")[:8]
    fixes = [d for d in history.get("discussions", []) if d.get("type") == "Issue" and d.get("state") == "open"]
    decisions = [d for d in history.get("decisions", []) if d.get("drift")]
    reviewers = _reviewers(graph_name, fix)
    blast = fix.get("blast_radius") or {}

    lines = [f"Fixes a bug found by Codebase Detective: **{inv.get('title', '')}**", ""]
    lines += ["## Root cause", f"`{fn['name']}` in `{fn['file_path']}`. {verdict.get('explanation', '')}".strip()]
    if fix.get("cause"):
        lines.append(f"\n{fix['cause']}")
    if decisions:
        lines += ["", "## Decision this restores"]
        for d in decisions[:2]:
            drift = d["drift"][0]
            lines.append(f"> {d['text']}\n\n{d.get('author') or 'The team'} recorded this in "
                         f"{d.get('item_type', '')} #{d.get('item_number')}. It was later reversed by "
                         f"\"{drift['message'][:80]}\"" + (f" (PR #{drift['pr']})" if drift.get("pr") else "") + ".")
    lines += ["", "## Proof", f"**{STATUS_TEXT[ver['status']]}**", "",
              "| Test | Original code | With this fix |", "|---|---|---|"]
    icon = {"passed": "pass", "failed": "FAIL", "error": "ERROR", "skipped": "skip", "missing": "-"}
    for r in ver["comparison"]:
        label = f"`{r['name']}`" + (" (new regression test)" if r["regression"] else "")
        lines.append(f"| {label} | {icon.get(r['before'], r['before'])} | {icon.get(r['after'], r['after'])} |")
    lines += ["", f"Tests were selected from the code graph: {len(ver['selected_tests'])} of {ver['total_tests']} "
              f"tests reach `{fn['name']}` through `TESTS` and `CALLS` edges."]
    if blast:
        lines.append(f"Blast radius: {blast.get('affected_functions', 0)} function(s) call into it"
                     + (f" ({', '.join(blast.get('sample_callers', [])[:3])})" if blast.get("sample_callers") else "")
                     + ".")
    if reviewers:
        lines += ["", "## Suggested reviewers"]
        lines += [f"- {r['name']}" + (f" (@{r['login']})" if r["login"] else "") + f": {r['why']}" for r in reviewers]
    if fixes:
        lines += [""] + [f"Fixes #{i['number']}" for i in fixes[:3]]
    lines += ["", "---", f"Investigation `{inv['id']}`. Every claim above comes from a node in the FalkorDB graph."]
    return {
        "title": f"Fix: {inv.get('title', '')}"[:120],
        "branch": f"detective/fix-{short}",
        "body": "\n".join(lines),
        "reviewers": reviewers,
        "fixes_issues": [i["number"] for i in fixes[:3]],
    }


def list_fixes(graph_name: str, investigation_id: str) -> List[Dict[str, Any]]:
    rows = get_falkordb().ro(graph_name, Q.LIST_FIXES, {"investigation_id": investigation_id})
    return [json.loads(r["payload"]) for r in rows if r["payload"]]
