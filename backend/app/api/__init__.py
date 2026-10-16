from fastapi import APIRouter

from app.api.routes import graph, investigations, repos
from app.core.config import settings
from app.core.falkordb import get_falkordb
from app.services.llm import get_llm

api_router = APIRouter()
api_router.include_router(repos.router, prefix="/repos", tags=["repositories"])
api_router.include_router(investigations.router, prefix="/investigations", tags=["investigations"])
api_router.include_router(graph.router, prefix="/graph", tags=["graph"])


@api_router.get("/health")
async def health_check():
    try:
        falkordb_ok = get_falkordb().ping()
    except Exception:
        falkordb_ok = False
    llm = get_llm()
    models = await llm.available_models()
    agent_model = await llm.pick_model(settings.OLLAMA_AGENT_MODEL) if models else None
    return {
        "status": "healthy" if falkordb_ok else "degraded",
        "falkordb": falkordb_ok,
        "llm": {"enabled": llm.enabled, "host": settings.OLLAMA_HOST, "models": models, "agent_model": agent_model},
        "version": settings.VERSION,
    }
