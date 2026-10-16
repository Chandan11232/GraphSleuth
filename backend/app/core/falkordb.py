"""Thin wrapper around the official FalkorDB client.

Every query goes through `query()` so results come back as plain dicts and
nodes/edges are converted to JSON-friendly structures.
"""
from typing import Any, Dict, List, Optional

import structlog
from falkordb import FalkorDB
from falkordb.edge import Edge
from falkordb.node import Node
from falkordb.path import Path

from app.core.config import settings

logger = structlog.get_logger()


def to_python(value: Any) -> Any:
    if isinstance(value, Node):
        return {
            "_kind": "node",
            "_id": value.id,
            "labels": list(value.labels or []),
            "properties": dict(value.properties or {}),
        }
    if isinstance(value, Edge):
        return {
            "_kind": "edge",
            "_id": value.id,
            "type": value.relation,
            "src": value.src_node if isinstance(value.src_node, int) else value.src_node.id,
            "dst": value.dest_node if isinstance(value.dest_node, int) else value.dest_node.id,
            "properties": dict(value.properties or {}),
        }
    if isinstance(value, Path):
        return {
            "_kind": "path",
            "nodes": [to_python(n) for n in value.nodes()],
            "edges": [to_python(e) for e in value.edges()],
        }
    if isinstance(value, list):
        return [to_python(v) for v in value]
    if isinstance(value, dict):
        return {k: to_python(v) for k, v in value.items()}
    return value


class FalkorDBConnection:
    def __init__(self) -> None:
        self._db: Optional[FalkorDB] = None

    @property
    def db(self) -> FalkorDB:
        if self._db is None:
            self._db = FalkorDB(
                host=settings.FALKORDB_HOST,
                port=settings.FALKORDB_PORT,
                username=settings.FALKORDB_USERNAME,
                password=settings.FALKORDB_PASSWORD,
            )
        return self._db

    def ping(self) -> bool:
        return bool(self.db.connection.ping())

    def query(
        self,
        graph_name: str,
        cypher: str,
        params: Optional[Dict[str, Any]] = None,
        read_only: bool = False,
    ) -> List[Dict[str, Any]]:
        graph = self.db.select_graph(graph_name)
        # The server default (1s) is too tight on a busy laptop running a local LLM.
        timeout = settings.QUERY_TIMEOUT_MS if read_only else settings.WRITE_TIMEOUT_MS
        try:
            if read_only:
                result = graph.ro_query(cypher, params, timeout=timeout)
            else:
                result = graph.query(cypher, params, timeout=timeout)
        except Exception as e:
            logger.error("Cypher failed", graph=graph_name, query=" ".join(cypher.split())[:300], error=str(e))
            raise
        headers = [h[1] if isinstance(h, (list, tuple)) else h for h in (result.header or [])]
        return [dict(zip(headers, (to_python(v) for v in row))) for row in result.result_set]

    def ro(self, graph_name: str, cypher: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        return self.query(graph_name, cypher, params, read_only=True)

    def list_graphs(self) -> List[str]:
        return list(self.db.list_graphs())

    def graph_exists(self, graph_name: str) -> bool:
        return graph_name in self.list_graphs()

    def delete_graph(self, graph_name: str) -> bool:
        try:
            self.db.select_graph(graph_name).delete()
            return True
        except Exception:
            return False


falkordb = FalkorDBConnection()


def get_falkordb() -> FalkorDBConnection:
    return falkordb
