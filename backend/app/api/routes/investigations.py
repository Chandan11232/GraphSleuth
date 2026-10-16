import asyncio
import json
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.core.falkordb import get_falkordb
from app.models.schemas import ConfirmRootCause, InvestigationCreate
from app.services import cypher_queries as Q
from app.services import store
from app.services.agent import Investigation, new_id

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
