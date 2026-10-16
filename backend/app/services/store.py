"""Repository registry and investigation lookup, persisted in FalkorDB.

The registry graph holds one Repository node per connected repo and an
InvestigationRef per investigation so an investigation id can be resolved to
its repository graph. Bug reports and investigations themselves live in the
repository's own graph (see agent memory in cypher_queries).
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.falkordb import get_falkordb

REG = settings.REGISTRY_GRAPH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repo(props: Dict[str, Any]) -> Dict[str, Any]:
    repo = dict(props)
    repo["stats"] = json.loads(repo.pop("stats_json", "{}") or "{}")
    for key in ("error_message", "ingested_at", "progress"):
        repo.setdefault(key, None)
    return repo


def create_repo(repo: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    props = {**repo, "status": "pending", "stats_json": "{}", "created_at": now, "updated_at": now}
    get_falkordb().query(REG, "CREATE (r:Repository) SET r = $props", {"props": props})
    return _repo(props)


def update_repo(repo_id: str, **fields: Any) -> None:
    if "stats" in fields:
        fields["stats_json"] = json.dumps(fields.pop("stats"))
    fields["updated_at"] = _now()
    get_falkordb().query(REG, "MATCH (r:Repository {id: $id}) SET r += $fields", {"id": repo_id, "fields": fields})


def get_repo(repo_id: str) -> Optional[Dict[str, Any]]:
    rows = get_falkordb().query(REG, "MATCH (r:Repository {id: $id}) RETURN r", {"id": repo_id})
    return _repo(rows[0]["r"]["properties"]) if rows else None


def list_repos() -> List[Dict[str, Any]]:
    rows = get_falkordb().query(REG, "MATCH (r:Repository) RETURN r ORDER BY r.created_at DESC")
    return [_repo(r["r"]["properties"]) for r in rows]


def delete_repo(repo_id: str) -> None:
    get_falkordb().query(REG, """
        MATCH (r:Repository {id: $id})
        OPTIONAL MATCH (r)-[:HAS_INVESTIGATION]->(i:InvestigationRef)
        DELETE i, r
    """, {"id": repo_id})


def register_investigation(investigation_id: str, repo_id: str, title: str) -> None:
    get_falkordb().query(REG, """
        MATCH (r:Repository {id: $repo_id})
        CREATE (r)-[:HAS_INVESTIGATION]->(:InvestigationRef {id: $id, title: $title, created_at: $at})
    """, {"repo_id": repo_id, "id": investigation_id, "title": title, "at": _now()})


def repo_for_investigation(investigation_id: str) -> Optional[Dict[str, Any]]:
    rows = get_falkordb().query(REG, """
        MATCH (r:Repository)-[:HAS_INVESTIGATION]->(:InvestigationRef {id: $id}) RETURN r
    """, {"id": investigation_id})
    return _repo(rows[0]["r"]["properties"]) if rows else None


def ensure_registry() -> None:
    db = get_falkordb()
    for q in ("CREATE INDEX FOR (r:Repository) ON (r.id)", "CREATE INDEX FOR (i:InvestigationRef) ON (i.id)"):
        try:
            db.query(REG, q)
        except Exception:
            pass  # index already exists
    # Ingestion running when the server stopped will never finish.
    db.query(REG, "MATCH (r:Repository) WHERE r.status IN ['pending', 'ingesting'] "
                  "SET r.status = 'error', r.error_message = 'Interrupted by a server restart; delete and reconnect.'")
