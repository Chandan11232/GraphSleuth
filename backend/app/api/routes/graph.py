from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from app.api.routes.investigations import load_investigation
from app.core.falkordb import get_falkordb
from app.services import cypher_queries as Q
from app.services import store, test_writer
from app.services.graph_tools import GraphTools

router = APIRouter()

KEEP_PROPS = ("id", "name", "qualified_name", "file_path", "path", "start_line", "end_line", "signature",
              "complexity", "message", "author", "timestamp", "sha", "email", "title", "status", "language",
              "number", "url", "state", "text", "login")


def node_out(n: Dict[str, Any]) -> Dict[str, Any]:
    props = n["properties"]
    label = n["labels"][0] if n["labels"] else "Node"
    if label in ("Issue", "PullRequest"):
        name = f"#{props.get('number')} {props.get('title', '')}"
    elif label == "Decision":
        name = (props.get("text") or "")[:60]
    elif label == "Comment":
        name = (props.get("body") or "")[:60]
    elif label == "Team":
        name = f"@{props.get('name')}"
    else:
        name = (props.get("qualified_name") or props.get("name") or props.get("title") or props.get("path")
                or (props.get("message") or "")[:40] or props.get("email") or str(n["_id"]))
    return {"id": str(n["_id"]), "type": label, "label": name,
            "properties": {k: props[k] for k in KEEP_PROPS if k in props}}


def edge_out(e: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": f"e{e['_id']}", "source": str(e["src"]), "target": str(e["dst"]), "type": e["type"],
            "properties": e["properties"]}


def repo_or_404(repo_id: str) -> Dict[str, Any]:
    repo = store.get_repo(repo_id)
    if not repo or repo["status"] != "ready":
        raise HTTPException(status_code=404, detail="Repository not found or not ready")
    return repo


@router.get("/investigation/{investigation_id}")
async def evidence_graph(investigation_id: str) -> Dict[str, Any]:
    """The subgraph the agent actually touched: bug report, every function, test,
    commit and person it visited, and the real edges between them."""
    result = load_investigation(investigation_id)
    if not result:
        raise HTTPException(status_code=404, detail="Investigation not found")
    repo = store.repo_for_investigation(investigation_id)
    ids: List[str] = list(result.get("evidence_ids", [])) + [result["bug_report_id"], investigation_id]
    nodes, edges = subgraph(repo["graph_name"], ids)

    by_fn = {s["id"]: s for s in result.get("suspects", [])}
    verdict_id = (result.get("verdict") or {}).get("id")
    for n in nodes.values():
        fid = n["properties"].get("id")
        if fid in by_fn:
            n["properties"]["suspicion_score"] = by_fn[fid]["suspicion_score"]
            n["properties"]["rank"] = by_fn[fid]["rank"]
        if fid and fid == verdict_id:
            n["properties"]["root_cause"] = True
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def subgraph(graph_name: str, ids: List[str]):
    """The nodes with these keys (id, commit sha or person email) and the real edges between them."""
    db = get_falkordb()
    nodes: Dict[str, Dict[str, Any]] = {}
    for row in db.ro(graph_name, "UNWIND $ids AS k MATCH (n) WHERE n.id = k OR n.sha = k OR n.email = k RETURN n",
                     {"ids": ids}):
        nodes[str(row["n"]["_id"])] = node_out(row["n"])
    edges: Dict[str, Dict[str, Any]] = {}
    for row in db.ro(graph_name, Q.SUBGRAPH_BETWEEN, {"ids": ids}):
        e = edge_out(row["r"])
        if e["source"] in nodes and e["target"] in nodes:
            edges[e["id"]] = e
    return nodes, edges


@router.get("/{repo_id}/function/{function_id}")
async def function_neighborhood(repo_id: str, function_id: str, limit: int = Query(40, ge=5, le=200)) -> Dict[str, Any]:
    repo = repo_or_404(repo_id)
    rows = get_falkordb().ro(repo["graph_name"], Q.NEIGHBORHOOD, {"id": function_id, "limit": limit})
    if not rows:
        raise HTTPException(status_code=404, detail="Function not found")
    nodes = {str(rows[0]["center"]["_id"]): node_out(rows[0]["center"])}
    edges = []
    for link in rows[0]["links"]:
        if not link["node"]:
            continue
        nodes[str(link["node"]["_id"])] = node_out(link["node"])
        edges.append(edge_out(link["rel"]))
    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/{repo_id}/hotspots")
async def hotspots(repo_id: str, limit: int = Query(10, ge=1, le=50)) -> List[Dict[str, Any]]:
    repo = repo_or_404(repo_id)
    return GraphTools(repo["graph_name"]).hotspots(limit).data


@router.get("/{repo_id}/risky-changes")
async def risky_changes(repo_id: str, commits: int = Query(10, ge=1, le=300)) -> List[Dict[str, Any]]:
    repo = repo_or_404(repo_id)
    return GraphTools(repo["graph_name"]).risky_changes(commits).data


@router.post("/{repo_id}/function/{function_id}/suggest-test")
async def suggest_test(repo_id: str, function_id: str) -> Dict[str, Any]:
    """Graph context for testing this function, plus a test written by the local model when one is available."""
    repo = repo_or_404(repo_id)
    try:
        return await test_writer.suggest_test(repo["graph_name"], function_id)
    except test_writer.FunctionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/{repo_id}/function/{function_id}/source")
async def function_source(repo_id: str, function_id: str) -> Dict[str, Any]:
    repo = repo_or_404(repo_id)
    res = GraphTools(repo["graph_name"]).get_function(function_id)
    if res.error:
        raise HTTPException(status_code=404, detail=res.summary)
    return res.data
