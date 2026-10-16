import asyncio
import uuid
from typing import Any, Dict

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.core.falkordb import get_falkordb
from app.models.schemas import RepositoryCreate
from app.services import cypher_queries as Q
from app.services import store
from app.services.code_graph import ingest_source

logger = structlog.get_logger()
router = APIRouter()


def graph_name_for(repo_id: str) -> str:
    return f"repo_{uuid.UUID(repo_id).hex}"


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_repository(data: RepositoryCreate, background_tasks: BackgroundTasks) -> Dict[str, Any]:
    repo_id = str(uuid.uuid4())
    repo = store.create_repo({
        "id": repo_id, "name": data.name, "url": data.url, "branch": data.branch or "",
        "graph_name": graph_name_for(repo_id),
    })
    background_tasks.add_task(ingest_task, repo_id, data.url, data.branch, data.github_token)
    return repo


async def ingest_task(repo_id: str, url: str, branch, token) -> None:
    repo = store.get_repo(repo_id)
    if not repo:
        return
    store.update_repo(repo_id, status="ingesting", progress="starting")

    def progress(stage: str, stats: Dict[str, int]) -> None:
        store.update_repo(repo_id, progress=stage)

    try:
        stats = await asyncio.to_thread(
            ingest_source, url, repo["graph_name"], repo_id, repo["name"], branch or None, token, progress)
        store.update_repo(repo_id, status="ready", stats=stats, progress="done",
                          ingested_at=store._now(), error_message="")
        logger.info("Repository ingested", repo_id=repo_id, stats=stats)
    except Exception as e:
        store.update_repo(repo_id, status="error", error_message=str(e)[:500], progress="failed")
        logger.error("Repository ingestion failed", repo_id=repo_id, error=str(e))


@router.get("")
async def list_repositories() -> Dict[str, Any]:
    repos = store.list_repos()
    return {"repositories": repos, "total": len(repos)}


@router.get("/{repo_id}")
async def get_repository(repo_id: str) -> Dict[str, Any]:
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.get("/{repo_id}/stats")
async def get_repository_stats(repo_id: str) -> Dict[str, Any]:
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail="Repository is not ready yet")
    db = get_falkordb()
    g = repo["graph_name"]
    nodes = {r["type"]: r["count"] for r in db.ro(g, Q.REPO_STATS)}
    edges = {r["type"]: r["count"] for r in db.ro(g, Q.EDGE_STATS)}
    languages = {r["language"]: r["count"] for r in db.ro(g, Q.LANGUAGE_STATS)}
    return {"nodes": nodes, "edges": edges, "languages": languages,
            "total_nodes": sum(nodes.values()), "total_edges": sum(edges.values())}


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(repo_id: str) -> None:
    repo = store.get_repo(repo_id)
    if not repo:
        raise HTTPException(status_code=404, detail="Repository not found")
    get_falkordb().delete_graph(repo["graph_name"])
    store.delete_repo(repo_id)
