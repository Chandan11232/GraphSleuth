from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    FALKORDB_HOST: str = "localhost"
    FALKORDB_PORT: int = 6379
    FALKORDB_USERNAME: Optional[str] = None
    FALKORDB_PASSWORD: Optional[str] = None
    FALKORDB_SSL: bool = False
    # Graph that lists repositories; each repository gets its own graph (repo_<hex>).
    REGISTRY_GRAPH: str = "cd_registry"
    QUERY_TIMEOUT_MS: int = 15000
    WRITE_TIMEOUT_MS: int = 120000

    # LLM_PROVIDER=none runs the graph-only investigation (no LLM needed).
    LLM_PROVIDER: str = "ollama"
    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    OLLAMA_API_KEY: Optional[str] = None
    # Model that drives the tool-calling investigation loop (must support tools).
    OLLAMA_AGENT_MODEL: str = "llama3.2"
    # Model that writes the final patch.
    OLLAMA_MODEL: str = "deepseek-coder-v2:16b"
    LLM_TIMEOUT_SECONDS: float = 120.0
    # How long Ollama keeps a model in RAM after a request ("1m", "5m", "0" = unload now).
    OLLAMA_KEEP_ALIVE: str = "1m"
    MAX_AGENT_TOOL_CALLS: int = 5

    GITHUB_TOKEN: Optional[str] = None
    COMMIT_HISTORY_LIMIT: int = 300
    MAX_FILES_PER_REPO: int = 5000
    # Lets POST /repos ingest a directory on the server (demo / offline use).
    ALLOW_LOCAL_REPOS: bool = True
    # When set, local repositories must live under this directory (set it on public deployments).
    LOCAL_REPOS_ROOT: Optional[str] = None

    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Codebase Detective"
    VERSION: str = "2.0.0"


settings = Settings()
