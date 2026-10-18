from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.core.config import settings
from app.services import store

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up", project=settings.PROJECT_NAME, version=settings.VERSION)
    try:
        store.ensure_registry()
        logger.info("FalkorDB connected", host=settings.FALKORDB_HOST, port=settings.FALKORDB_PORT)
    except Exception as e:
        logger.error("FalkorDB connection failed", error=str(e))
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="GraphSleuth: a bug-investigation agent that reasons over a FalkorDB code graph.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception", path=request.url.path, error=str(exc))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/")
async def root():
    return {"name": settings.PROJECT_NAME, "version": settings.VERSION, "docs": "/docs",
            "health": f"{settings.API_V1_PREFIX}/health"}
