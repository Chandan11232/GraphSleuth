"""MCP server: exposes the code graph tools to any MCP client (Claude Code,
Claude Desktop, Cursor, ...).

Investigations run here are written to the same graph as the web app, so the
web agent and an external agent share one memory.

Run:  python -m app.mcp_server            (stdio transport)
"""
import json
import logging
import sys
from typing import Any, Dict, List, Optional

import structlog
from mcp.server.fastmcp import FastMCP

from app.core.falkordb import get_falkordb
from app.services import cypher_queries as Q
from app.services import store
from app.services.agent import Investigation, new_id
from app.services.brain import Brain
from app.services.graph_tools import GraphTools, ToolResult
from app.services.verifier import Verification, impacted_tests

# stdout carries the MCP protocol, so every log line must go to stderr.
# (structlog loggers are lazy, so configuring after the imports still applies.)
logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

mcp = FastMCP(
    "graphsleuth",
    instructions=(
        "Tools over a FalkorDB code graph (functions, CALLS, tests, commits, authors), the team knowledge around it "
        "(issues, pull requests, reviews, decisions, CODEOWNERS) and a memory of past bug investigations. Start with "
        "list_repositories, then search_code / investigate_bug, or ask_company_brain / why_code / who_owns. After an "
        "investigation, write the corrected function and a regression test and prove them with verify_fix. Pass "
        "the repository name or id to every tool. Each result includes the Cypher that produced it."
    ),
)


def _repo(repository: str) -> Dict[str, Any]:
    repos = store.list_repos()
    for r in repos:
        if repository in (r["id"], r["name"]) and r["status"] == "ready":
            return r
    ready = [r["name"] for r in repos if r["status"] == "ready"]
    raise ValueError(f"No ready repository '{repository}'. Available: {', '.join(ready) or 'none'}")


def _out(res: ToolResult) -> str:
    return json.dumps({"summary": res.summary, "data": res.data, "cypher": res.cypher}, default=str, indent=1)


@mcp.tool()
def list_repositories() -> str:
    """Repositories that have been ingested into the code graph."""
    return json.dumps([{"name": r["name"], "id": r["id"], "status": r["status"], "stats": r["stats"]}
                       for r in store.list_repos()], indent=1)


@mcp.tool()
def search_code(repository: str, query: str) -> str:
    """Full-text search for functions by words or identifiers."""
    return _out(GraphTools(_repo(repository)["graph_name"]).search_code(query))


@mcp.tool()
def get_function(repository: str, function: str) -> str:
    """Source code, signature and location of a function (name, Class.method, or id)."""
    return _out(GraphTools(_repo(repository)["graph_name"]).get_function(function))


@mcp.tool()
def get_callers(repository: str, function: str, depth: int = 2) -> str:
    """Functions that call `function`, up to `depth` (1-3) hops away."""
    return _out(GraphTools(_repo(repository)["graph_name"]).get_callers(function, depth))


@mcp.tool()
def get_callees(repository: str, function: str, depth: int = 1) -> str:
    """Repository functions that `function` calls, up to `depth` (1-3) hops away."""
    return _out(GraphTools(_repo(repository)["graph_name"]).get_callees(function, depth))


@mcp.tool()
def get_tests(repository: str, functions: List[str]) -> str:
    """Tests that cover each function directly or through a caller."""
    return _out(GraphTools(_repo(repository)["graph_name"]).get_tests(functions))


@mcp.tool()
def recent_changes(repository: str, functions: List[str]) -> str:
    """Recent commits (with authors) whose diffs touched each function."""
    return _out(GraphTools(_repo(repository)["graph_name"]).recent_changes(functions))


@mcp.tool()
def find_experts(repository: str, function: str) -> str:
    """People who changed this function or its file: good reviewers."""
    return _out(GraphTools(_repo(repository)["graph_name"]).find_experts(function))


@mcp.tool()
def blast_radius(repository: str, function: str) -> str:
    """What a change to `function` affects: transitive callers, files and covering tests."""
    return _out(GraphTools(_repo(repository)["graph_name"]).blast_radius(function))


@mcp.tool()
def call_path(repository: str, from_function: str, to_function: str) -> str:
    """Shortest chain of calls from one function to another."""
    return _out(GraphTools(_repo(repository)["graph_name"]).call_path(from_function, to_function))


@mcp.tool()
def hotspots(repository: str, limit: int = 10) -> str:
    """Most central functions (PageRank over the call graph), with their test counts."""
    return _out(GraphTools(_repo(repository)["graph_name"]).hotspots(limit))


@mcp.tool()
def risky_changes(repository: str, commits: int = 10) -> str:
    """Untested functions changed by the last `commits` commits, with who changed them, the commit
    messages and how many functions call them (most-called first). Use it to review recent risky
    changes or when a bug appeared recently."""
    return _out(GraphTools(_repo(repository)["graph_name"]).risky_changes(commits))


@mcp.tool()
def test_context(repository: str, function: str) -> str:
    """Everything needed to write a unit test for `function`: its source, how its callers use it,
    what it calls, and the nearest existing tests in the repo to copy the style of."""
    return _out(GraphTools(_repo(repository)["graph_name"]).test_context(function))


@mcp.tool()
def recall_past_investigations(repository: str, functions: Optional[List[str]] = None, text: str = "") -> str:
    """Earlier bug investigations involving these functions (or similar wording)."""
    return _out(GraphTools(_repo(repository)["graph_name"]).recall_past_investigations(functions or [], text=text))


@mcp.tool()
def why_code(repository: str, function: str) -> str:
    """Why a function is the way it is: the commits and pull requests that changed it, who opened and
    reviewed them, the issues they fixed, recorded decisions that govern it (with later changes that may
    break them), and open issues that mention it."""
    return _out(GraphTools(_repo(repository)["graph_name"]).why_code(function))


@mcp.tool()
def who_owns(repository: str, target: str) -> str:
    """Who to ask about a file path or function: CODEOWNERS teams and members, authors and reviewers,
    ranked with the reason for each person."""
    return _out(GraphTools(_repo(repository)["graph_name"]).who_owns(target))


@mcp.tool()
def search_knowledge(repository: str, query: str) -> str:
    """Full-text search over the team's issues, pull requests, review comments and recorded decisions."""
    return _out(GraphTools(_repo(repository)["graph_name"]).search_knowledge(query))


@mcp.tool()
def decisions(repository: str) -> str:
    """Decisions recorded in issues, pull requests and reviews, the code each one governs, and any later
    commits that changed that code (knowledge drift)."""
    return _out(GraphTools(_repo(repository)["graph_name"]).decisions())


@mcp.tool()
async def ask_company_brain(repository: str, question: str) -> str:
    """Answer a question about the codebase and the team around it ("why does X do Y", "who should review
    Z", "what did we decide about W"), citing the issue, pull request, comment or decision behind each claim."""
    result = await Brain(_repo(repository)["graph_name"]).ask(question, use_llm=False)
    return json.dumps({k: result[k] for k in ("answer", "sources", "steps")}, default=str, indent=1)


@mcp.tool()
async def investigate_bug(repository: str, title: str, description: str) -> str:
    """Run the full graph investigation on a bug report (graph-only, no LLM) and save it to shared memory.
    Returns ranked suspects with score breakdowns, the verdict, and the reasoning steps."""
    repo = _repo(repository)
    inv_id = new_id()
    store.register_investigation(inv_id, repo["id"], title)
    agent = Investigation(repo["graph_name"], repo["id"], inv_id, new_id(), title, description, use_llm=False)
    result: Dict[str, Any] = {}
    async for event in agent.run():
        if event["type"] == "result":
            result = event["result"]
    return json.dumps({
        "investigation_id": inv_id,
        "verdict": result.get("verdict"),
        "suspects": [{k: s[k] for k in ("rank", "id", "name", "file_path", "suspicion_score", "breakdown", "reasoning")}
                     for s in result.get("suspects", [])[:6]],
        "reviewers": (result.get("fix") or {}).get("reviewers"),
        "steps": [f"{s['step']}. {s['action']}: {s['result_summary']}" for s in result.get("steps", [])],
    }, default=str, indent=1)


@mcp.tool()
def confirm_root_cause(repository: str, investigation_id: str, function: str, note: str = "") -> str:
    """Record the real root cause of an investigation. Future investigations recall it."""
    repo = _repo(repository)
    fn = GraphTools(repo["graph_name"]).resolve(function)
    if not fn:
        return f"No function matching '{function}'"
    rows = get_falkordb().query(repo["graph_name"], Q.CONFIRM_ROOT_CAUSE,
                                {"id": investigation_id, "fn_id": fn["id"], "note": note})
    return f"Confirmed {rows[0]['name']} as the root cause." if rows else "Investigation not found"


@mcp.tool()
def select_tests(repository: str, function: str) -> str:
    """Test impact analysis: the tests whose TESTS edge reaches the function directly or through up to 3 CALLS
    hops, i.e. the tests a change to it can break."""
    repo = _repo(repository)
    fn = GraphTools(repo["graph_name"]).resolve(function)
    if not fn:
        return f"No function matching '{function}'"
    impact = impacted_tests(repo["graph_name"], fn["id"])
    return json.dumps({"function": fn, "selected": impact["tests"], "total_tests": impact["total"],
                       "cypher": Q.TEST_IMPACT.strip()}, default=str, indent=1)


@mcp.tool()
async def verify_fix(repository: str, investigation_id: str, patched_code: str, test_code: str = "") -> str:
    """Prove a fix for an investigation's root cause in a sandbox copy of the repository. patched_code is the
    complete corrected root-cause function; test_code is a pytest regression test that should fail on the
    current code and pass with the fix. Runs it and the graph-selected tests before and after the patch, and
    saves the outcome (Fix, TestRun) to the graph. A verified fix confirms the root cause in shared memory."""
    from app.api.routes.investigations import load_investigation
    from app.services.sandbox import verification_allowed

    repo = _repo(repository)
    inv = load_investigation(investigation_id)
    if not inv:
        return "Investigation not found"
    blocked = verification_allowed(repo["url"])
    if blocked:
        return blocked
    result: Dict[str, Any] = {}
    errors: List[str] = []
    steps: List[str] = []
    async for event in Verification(repo, inv, patched_code=patched_code, test_code=test_code).run():
        if event["type"] == "step":
            steps.append(f"{event['step']['action']}: {event['step']['result_summary']}")
        elif event["type"] == "error":
            errors.append(event["message"])
        elif event["type"] == "result":
            result = event["result"]
    if errors:
        return json.dumps({"error": errors[-1], "steps": steps}, indent=1)
    return json.dumps({
        "fix_id": result["id"], "status": result["status"], "headline": result["headline"],
        "comparison": result["comparison"], "diff": result["diff"], "pull_request_draft": result["pr_draft"],
        "steps": steps,
        "failures": [t for t in result["after"]["tests"] if t["outcome"] != "passed"],
    }, default=str, indent=1)


@mcp.tool()
def run_cypher(repository: str, query: str) -> str:
    """Run a read-only Cypher query against the repository's code graph (GRAPH.RO_QUERY).
    Labels: Function, Class, File, Test, Commit, Person, BugReport, Investigation, Team, Issue, PullRequest,
    Comment, Decision, Fix, TestRun.
    Edges: CALLS, CONTAINS, HAS_METHOD, IMPORTS, TESTS, MODIFIES, TOUCHED, AUTHORED, MENTIONS,
    INVESTIGATED_IN, SUSPECTS, CONFIRMED, PROPOSED, PATCHES, VERIFIED_BY, RAN, OPENED_AS, MEMBER_OF, OWNS, OPENED, REVIEWED, WROTE, ON, ABOUT, FIXES,
    REFERENCES, INCLUDES, DECIDED_IN, DECIDED."""
    rows = get_falkordb().ro(_repo(repository)["graph_name"], query)
    return json.dumps(rows[:100], default=str, indent=1)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
