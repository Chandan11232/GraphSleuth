import asyncio
import json
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse, StreamingResponse

from app.core.config import settings
from app.core.falkordb import get_falkordb
from app.models.schemas import ConfirmRootCause, InvestigationCreate, OpenPullRequest, VerifyFix
from app.services import cypher_queries as Q
from app.services import sandbox, store, verifier
from app.services.agent import Investigation, new_id
from app.services.pull_request import PullRequestError, format_patch, github_repo, open_pull_request

logger = structlog.get_logger()
router = APIRouter()


class Run:
    """Events of an investigation that is still running in this process."""

    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.done = False
        self.changed = asyncio.Event()

    def push(self, event: Dict[str, Any]) -> None:
        self.events.append(event)
        self.changed.set()


RUNS: Dict[str, Run] = {}
_TASKS: set = set()
# Sandboxed test runs are CPU-heavy; a small server runs a couple at a time.
_VERIFY_SLOTS = asyncio.Semaphore(2)


def load_investigation(investigation_id: str) -> Optional[Dict[str, Any]]:
    repo = store.repo_for_investigation(investigation_id)
    if not repo:
        return None
    rows = get_falkordb().ro(repo["graph_name"], Q.LOAD_INVESTIGATION, {"id": investigation_id})
    if not rows or not rows[0]["payload"]:
        return None
    result = json.loads(rows[0]["payload"])
    result["repository_name"] = repo["name"]
    confirmed = get_falkordb().ro(repo["graph_name"], """
        MATCH (:Investigation {id: $id})-[c:CONFIRMED]->(f:Function)
        RETURN f.id AS id, f.qualified_name AS name, c.note AS note
    """, {"id": investigation_id})
    result["confirmed"] = confirmed
    if confirmed:
        result["status"] = "confirmed"
    return result


@router.post("", status_code=status.HTTP_201_CREATED)
async def start_investigation(data: InvestigationCreate) -> Dict[str, Any]:
    repo = store.get_repo(data.repository_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"Repository is {repo['status']}, not ready")

    investigation_id, bug_id = new_id(), new_id()
    store.register_investigation(investigation_id, repo["id"], data.title)
    agent = Investigation(repo["graph_name"], repo["id"], investigation_id, bug_id,
                          data.title, data.description, use_llm=data.use_llm)
    run = Run()
    RUNS[investigation_id] = run

    async def drive() -> None:
        try:
            async for event in agent.run():
                run.push(event)
        except Exception as e:
            logger.error("Investigation failed", id=investigation_id, error=str(e))
            run.push({"type": "error", "message": str(e)[:300]})
        finally:
            run.done = True
            run.changed.set()

    task = asyncio.create_task(drive())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return {"id": investigation_id, "bug_report_id": bug_id, "repository_id": repo["id"], "status": "running"}


@router.get("")
async def list_investigations(repository_id: str = Query(...), limit: int = Query(20, ge=1, le=100)) -> List[Dict[str, Any]]:
    repo = store.get_repo(repository_id)
    if not repo or repo["status"] != "ready":
        return []
    rows = get_falkordb().ro(repo["graph_name"], Q.LIST_INVESTIGATIONS, {"limit": limit})
    for r in rows:
        if r["id"] in RUNS and not RUNS[r["id"]].done:
            r["status"] = "running"
    return rows


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str) -> Dict[str, Any]:
    run = RUNS.get(investigation_id)
    if run and not run.done:
        steps = [e["step"] for e in run.events if e["type"] == "step"]
        return {"id": investigation_id, "status": "running", "steps": steps, "suspects": [], "verdict": {}, "fix": {}}
    result = load_investigation(investigation_id)
    if not result:
        if run:
            errors = [e for e in run.events if e["type"] == "error"]
            if errors:
                return {"id": investigation_id, "status": "failed", "error": errors[-1]["message"],
                        "steps": [e["step"] for e in run.events if e["type"] == "step"]}
        raise HTTPException(status_code=404, detail="Investigation not found")
    return result


@router.get("/{investigation_id}/events")
async def stream_investigation(investigation_id: str) -> StreamingResponse:
    """Server-sent events: every reasoning step as it happens, then the result."""
    run = RUNS.get(investigation_id)
    if run is None and load_investigation(investigation_id) is None:
        raise HTTPException(status_code=404, detail="Investigation not found")

    async def events():
        if run is None:  # already finished in an earlier server session: replay it
            result = load_investigation(investigation_id)
            for step in result["steps"]:
                yield f"data: {json.dumps({'type': 'step', 'step': step}, default=str)}\n\n"
            yield f"data: {json.dumps({'type': 'result', 'result': result}, default=str)}\n\n"
            yield "data: [DONE]\n\n"
            return
        sent = 0
        while True:
            while sent < len(run.events):
                yield f"data: {json.dumps(run.events[sent], default=str)}\n\n"
                sent += 1
            if run.done:
                break
            run.changed.clear()
            try:
                await asyncio.wait_for(run.changed.wait(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/{investigation_id}/confirm")
async def confirm_root_cause(investigation_id: str, body: ConfirmRootCause) -> Dict[str, Any]:
    """Human feedback: mark the real root cause. Stored as a CONFIRMED edge that
    future investigations recall."""
    repo = store.repo_for_investigation(investigation_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Investigation not found")
    rows = get_falkordb().query(repo["graph_name"], Q.CONFIRM_ROOT_CAUSE,
                                {"id": investigation_id, "fn_id": body.function_id, "note": body.note})
    if not rows:
        raise HTTPException(status_code=404, detail="Function or investigation not found in the graph")
    return {"confirmed": rows[0]["name"]}


# ---------------------------------------------------------------- closing the loop

def _finished(investigation_id: str) -> tuple:
    repo = store.repo_for_investigation(investigation_id)
    result = load_investigation(investigation_id) if repo else None
    if not repo or not result:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return repo, result


@router.post("/{investigation_id}/fix/draft")
async def draft_fix(investigation_id: str) -> Dict[str, Any]:
    """The corrected function, a regression test and the tests the graph selected, for review before verifying."""
    repo, result = _finished(investigation_id)
    try:
        out = await verifier.draft(repo["graph_name"], result)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    out["blocked"] = sandbox.verification_allowed(repo["url"])
    out["can_open_pr"] = bool(github_repo(repo["url"]))
    out["server_token"] = bool(settings.GITHUB_TOKEN)
    return out


@router.post("/{investigation_id}/verify")
async def verify_fix(investigation_id: str, body: VerifyFix) -> StreamingResponse:
    """Server-sent events: apply the fix in a sandbox copy of the repository, run the regression test and the
    tests the graph selected before and after, and save the outcome to the graph."""
    repo, result = _finished(investigation_id)
    blocked = sandbox.verification_allowed(repo["url"])
    if blocked:
        raise HTTPException(status_code=403, detail=blocked)
    ver = verifier.Verification(repo, result, patched_code=body.patched_code, diff=body.diff,
                                test_code=body.test_code, test_path=body.test_path)

    async def events():
        async with _VERIFY_SLOTS:
            try:
                async for event in ver.run():
                    yield f"data: {json.dumps(event, default=str)}\n\n"
            except Exception as e:
                logger.error("Verification failed", id=investigation_id, error=str(e))
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{investigation_id}/fixes")
async def list_fixes(investigation_id: str) -> List[Dict[str, Any]]:
    repo = store.repo_for_investigation(investigation_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return verifier.list_fixes(repo["graph_name"], investigation_id)


def _fix(investigation_id: str, fix_id: str) -> tuple:
    repo = store.repo_for_investigation(investigation_id)
    rows = get_falkordb().ro(repo["graph_name"], Q.GET_FIX, {"id": fix_id}) if repo else []
    if not rows or rows[0]["investigation_id"] != investigation_id:
        raise HTTPException(status_code=404, detail="Fix not found")
    return repo, json.loads(rows[0]["payload"])


@router.get("/{investigation_id}/fixes/{fix_id}/patch", response_class=PlainTextResponse)
async def download_patch(investigation_id: str, fix_id: str) -> PlainTextResponse:
    _, ver = _fix(investigation_id, fix_id)
    return PlainTextResponse(format_patch(ver), media_type="text/x-patch", headers={
        "Content-Disposition": f'attachment; filename="{ver["pr_draft"]["branch"].split("/")[-1]}.patch"'})


@router.post("/{investigation_id}/fixes/{fix_id}/pull-request")
async def create_pull_request(investigation_id: str, fix_id: str, body: OpenPullRequest) -> Dict[str, Any]:
    """Open the pull request for a fix. Needs a person's approval; an unverified fix opens as a draft."""
    if not body.approve:
        raise HTTPException(status_code=400, detail="Opening a pull request needs explicit approval (approve=true).")
    repo, ver = _fix(investigation_id, fix_id)
    if ver.get("pull_request"):
        return ver["pull_request"]
    token = body.github_token or settings.GITHUB_TOKEN
    if not token:
        raise HTTPException(status_code=400, detail="A GitHub token with write access to the repository is needed.")
    try:
        pr = await open_pull_request(repo["url"], token, ver)
    except PullRequestError as e:
        raise HTTPException(status_code=409, detail=str(e))
    ver["pull_request"] = pr
    db = get_falkordb()
    db.query(repo["graph_name"], Q.UPDATE_FIX, {"id": fix_id, "status": ver["status"],
                                                "payload": json.dumps(ver, default=str)})
    db.query(repo["graph_name"], Q.SAVE_OPENED_PR, {
        "fix_id": fix_id, "pr_id": f"pr-{pr['number']}", "number": pr["number"], "title": ver["pr_draft"]["title"],
        "url": pr["url"], "body": ver["pr_draft"]["body"]})
    return pr
