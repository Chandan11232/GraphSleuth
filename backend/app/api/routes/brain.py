"""Company brain: questions about the code and the team around it, ownership, decisions."""
import asyncio
import os
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from app.api.routes.graph import repo_or_404, subgraph
from app.core.config import settings
from app.core.falkordb import get_falkordb
from app.models.schemas import BrainQuestion
from app.services import cypher_queries as Q
from app.services.brain import Brain
from app.services.graph_tools import GraphTools
from app.services.knowledge import ingest_knowledge

router = APIRouter()


@router.post("/{repo_id}/ask")
async def ask(repo_id: str, body: BrainQuestion) -> Dict[str, Any]:
    repo = repo_or_404(repo_id)
    result = await Brain(repo["graph_name"]).ask(body.question, use_llm=body.use_llm)
    nodes, edges = subgraph(repo["graph_name"], result["evidence_ids"])
    result["graph"] = {"nodes": list(nodes.values()), "edges": list(edges.values())}
    return result


@router.get("/{repo_id}/overview")
async def overview(repo_id: str) -> Dict[str, Any]:
    """What the brain knows: knowledge sources, who owns what, decisions (and which have drifted),
    and files only one person has ever changed."""
    repo = repo_or_404(repo_id)
    tools = GraphTools(repo["graph_name"])
    status = get_falkordb().ro(repo["graph_name"], Q.KNOWLEDGE_STATUS)
    decisions = tools.decisions()
    gaps = tools.knowledge_gaps()
    ownership = tools.ownership()
    return {
        "status": status[0] if status else {},
        "ownership": ownership.data,
        "decisions": decisions.data,
        "knowledge_gaps": gaps.data,
        "summaries": {"decisions": decisions.summary, "knowledge_gaps": gaps.summary},
        "cypher": {"ownership": ownership.cypher, "decisions": decisions.cypher, "knowledge_gaps": gaps.cypher},
    }


@router.get("/{repo_id}/function/{function_id}/why")
async def why(repo_id: str, function_id: str) -> Dict[str, Any]:
    repo = repo_or_404(repo_id)
    res = GraphTools(repo["graph_name"]).why_code(function_id)
    if res.error:
        raise HTTPException(status_code=404, detail=res.summary)
    return {"summary": res.summary, "data": res.data, "cypher": res.cypher}


@router.get("/{repo_id}/owners")
async def owners(repo_id: str, target: str = Query(..., min_length=1, max_length=300)) -> Dict[str, Any]:
    repo = repo_or_404(repo_id)
    res = GraphTools(repo["graph_name"]).who_owns(target)
    if res.error:
        raise HTTPException(status_code=404, detail=res.summary)
    return {"summary": res.summary, "data": res.data, "cypher": res.cypher}


@router.post("/{repo_id}/sync")
async def sync(repo_id: str) -> Dict[str, Any]:
    """Keep knowledge current: re-read the tracker and MERGE what changed since the last sync."""
    repo = repo_or_404(repo_id)
    g = repo["graph_name"]
    status = get_falkordb().ro(g, Q.KNOWLEDGE_STATUS)
    since = status[0]["synced_at"] if status else None
    local = repo["url"] if os.path.isdir(repo["url"]) and settings.ALLOW_LOCAL_REPOS else None
    stats = await asyncio.to_thread(ingest_knowledge, g, local, repo["url"], settings.GITHUB_TOKEN, None, since)
    if stats.get("knowledge_error"):
        raise HTTPException(status_code=502, detail="Could not read the tracker (rate limit or network); try later")
    return {"since": since, "stats": stats}
