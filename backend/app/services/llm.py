"""Ollama client used for the tool-calling loop and for writing patches.

Works with a local Ollama or Ollama Cloud (set OLLAMA_HOST and OLLAMA_API_KEY).
With LLM_PROVIDER=none the agent runs graph-only and never calls this.
"""
from typing import Any, Dict, List, Optional

import ollama
import structlog

from app.core.config import settings

logger = structlog.get_logger()


# Ollama model families that support tool calling, best first.
TOOL_MODEL_FAMILIES = ("qwen2.5-coder", "qwen3", "qwen2.5", "llama3.1", "llama3.2", "mistral-nemo", "mistral",
                       "command-r", "granite3")


class LLMUnavailable(Exception):
    pass


class ToolsUnsupported(Exception):
    pass


class LLMClient:
    def __init__(self) -> None:
        headers = {"Authorization": f"Bearer {settings.OLLAMA_API_KEY}"} if settings.OLLAMA_API_KEY else None
        self.client = ollama.AsyncClient(host=settings.OLLAMA_HOST, headers=headers,
                                         timeout=settings.LLM_TIMEOUT_SECONDS)

    @property
    def enabled(self) -> bool:
        return settings.LLM_PROVIDER.lower() == "ollama"

    async def available_models(self) -> List[str]:
        if not self.enabled:
            return []
        try:
            resp = await self.client.list()
            return [m.model for m in resp.models]
        except Exception:
            return []

    async def has_model(self, name: str) -> bool:
        return await self.pick_model(name, fallbacks=()) is not None

    async def pick_model(self, preferred: str, fallbacks=TOOL_MODEL_FAMILIES) -> Optional[str]:
        """The preferred model if the server has it, else the first available model
        from `fallbacks` (families known to support tool calling)."""
        models = await self.available_models()
        for m in models:
            if m == preferred or m == f"{preferred}:latest" or m.split(":")[0] == preferred:
                return m
        for family in fallbacks:
            for m in models:
                if m.startswith(family):
                    return m
        return None

    async def chat(self, model: str, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
                   temperature: float = 0.1) -> Dict[str, Any]:
        if not self.enabled:
            raise LLMUnavailable("LLM disabled")
        try:
            resp = await self.client.chat(model=model, messages=messages, tools=tools,
                                          options={"temperature": temperature, "num_ctx": 8192},
                                          keep_alive=settings.OLLAMA_KEEP_ALIVE)
        except ollama.ResponseError as e:
            if "does not support tools" in str(e):
                raise ToolsUnsupported(str(e)) from e
            raise LLMUnavailable(str(e)) from e
        except Exception as e:
            raise LLMUnavailable(str(e)) from e
        msg = resp.message
        calls = [{"name": c.function.name, "arguments": dict(c.function.arguments or {})}
                 for c in (msg.tool_calls or [])]
        return {"content": msg.content or "", "tool_calls": calls}


_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
