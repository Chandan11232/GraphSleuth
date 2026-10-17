"""Suggests a unit test for one function.

The graph supplies the context (the function's code, how its callers use it, what
it calls, and the nearest existing tests to copy the style of); a local model writes
the test. Without a model the context alone is returned, which is still enough for
a person, or an MCP client like Claude Code, to write the test.
"""
import re
from pathlib import PurePosixPath
from typing import Any, Dict, Optional

from app.core.config import settings
from app.services.graph_tools import GraphTools
from app.services.llm import LLMUnavailable, get_llm

CODE_MODELS = ("qwen2.5-coder", "deepseek-coder", "codellama", "qwen3", "qwen2.5", "llama3.1", "gpt-oss")

FRAMEWORKS = {
    "python": "pytest",
    "javascript": "Jest",
    "typescript": "Jest",
    "tsx": "Jest with React Testing Library",
    "go": "Go's testing package",
    "java": "JUnit 5",
}

LANGUAGE_BY_EXT = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
                   ".cjs": "javascript", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
                   ".tsx": "tsx", ".go": "go", ".java": "java"}


class FunctionNotFound(Exception):
    pass


def framework_for(language: str, examples: list) -> str:
    sample = "\n".join(e.get("code") or "" for e in examples)
    if language in ("javascript", "typescript", "tsx") and re.search(r"from ['\"]vitest['\"]|\bvi\.", sample):
        return "Vitest"
    return FRAMEWORKS.get(language, "the project's test framework")


def suggest_test_path(file_path: str, language: str, examples: list) -> str:
    """Where the new test would live, following the repo's existing test layout."""
    p = PurePosixPath(file_path)
    if language == "python":
        folder = PurePosixPath(examples[0]["file_path"]).parent if examples else PurePosixPath("tests")
        return str(folder / f"test_{p.stem}.py")
    if language in ("javascript", "typescript", "tsx"):
        return str(p.with_name(f"{p.stem}.test{p.suffix}"))
    if language == "go":
        return str(p.with_name(f"{p.stem}_test.go"))
    if language == "java":
        return str(p.with_name(f"{p.stem}Test.java")).replace("src/main/", "src/test/")
    return str(p.with_name(f"test_{p.name}"))


def _block(title: str, items: list, limit: int = 1200) -> str:
    if not items:
        return ""
    parts = [f"# {i['file_path']} ({i['name']})\n{(i.get('code') or '')[:limit]}" for i in items]
    return f"{title}:\n```\n" + "\n\n".join(parts) + "\n```\n\n"


def build_prompt(ctx: Dict[str, Any], language: str, framework: str, test_path: str) -> str:
    return (
        f"Write a {framework} test file for the function `{ctx['name']}` in `{ctx['file_path']}`.\n"
        f"It will be saved as `{test_path}`, so import the code under test from its real module path.\n\n"
        f"Function under test:\n```\n{ctx['code']}\n```\n\n"
        + (f"The rest of `{ctx['file_path']}` outside its functions (imports, constants, module state):\n"
           f"```\n{ctx['module_code']}\n```\n\n" if ctx.get("module_code") else "")
        + _block("How the code calls it (its callers)", ctx["callers"])
        + _block("What it calls (set these up with real data, or fake them if they reach a network or database)",
                 ctx["callees"], 900)
        + _block("Existing tests in this repository, follow their style, imports and setup", ctx["example_tests"])
        + "Work out every expected value by reading the function line by line; do not guess what it does. Import "
        "every name you use, including module state you read or reset, and give each test its own data so the "
        "tests do not depend on each other. Cover the normal case, at least one edge case (empty, zero, missing or "
        "invalid input) and any error it raises or returns; for errors, check the exception type rather than its "
        "exact message. Use only functions, names and data (ids, keys) that exist in the code shown. Output a single ```code block``` with the complete test file, then one line starting with "
        "'Covers:' listing the cases in a few words."
    )


def parse_reply(text: str) -> Dict[str, str]:
    blocks = re.findall(r"```[a-zA-Z0-9_+-]*\n(.*?)```", text, re.S)
    code = max(blocks, key=len).strip() if blocks else ""
    covers = re.search(r"^\s*\**Covers:?\**:?\s*(.+)$", text, re.M)
    # Small models sometimes put the Covers line inside the code block, where it breaks the file.
    code = re.sub(r"\n\s*\**Covers:.*$", "", code, flags=re.S).rstrip()
    return {"code": code, "covers": covers.group(1).strip() if covers else ""}


async def suggest_test(graph_name: str, function_id: str, model: Optional[str] = None) -> Dict[str, Any]:
    ctx_result = GraphTools(graph_name).test_context(function_id)
    if ctx_result.error:
        raise FunctionNotFound(ctx_result.summary)
    ctx = ctx_result.data
    language = LANGUAGE_BY_EXT.get(PurePosixPath(ctx["file_path"]).suffix.lower(), "")
    framework = framework_for(language, ctx["example_tests"])
    test_path = suggest_test_path(ctx["file_path"], language, ctx["example_tests"])
    out: Dict[str, Any] = {
        "function": {"id": ctx["id"], "name": ctx["name"], "file_path": ctx["file_path"],
                     "start_line": ctx["start_line"]},
        "language": language,
        "framework": framework,
        "test_path": test_path,
        "code": "",
        "covers": "",
        "model": None,
        "error": None,
        "context": {
            "summary": ctx_result.summary,
            "caller_count": ctx["caller_count"],
            "callers": [{"name": c["name"], "file_path": c["file_path"]} for c in ctx["callers"]],
            "callees": [{"name": c["name"], "file_path": c["file_path"]} for c in ctx["callees"]],
            "example_tests": [{"name": t["name"], "file_path": t["file_path"]} for t in ctx["example_tests"]],
            "examples_are_nearby": ctx["examples_are_nearby"],
            "cypher": ctx_result.cypher,
        },
    }

    llm = get_llm()
    model = model or (await llm.pick_model(settings.OLLAMA_MODEL, fallbacks=CODE_MODELS) if llm.enabled else None)
    if not model:
        out["error"] = ("No local model is available, so only the graph context is shown. Start Ollama with "
                        f"{settings.OLLAMA_MODEL} to have a test written." if llm.enabled else
                        "This server runs graph-only (no LLM), so only the graph context a model would use is shown.")
        return out
    try:
        reply = await llm.chat(model, [
            {"role": "system", "content": "You are a senior engineer writing focused, runnable unit tests."},
            {"role": "user", "content": build_prompt(ctx, language, framework, test_path)},
        ])
    except LLMUnavailable as e:
        out["error"] = f"The model could not be reached: {str(e)[:200]}"
        return out
    out.update(parse_reply(reply["content"]), model=model)
    if not out["code"]:
        out["error"] = "The model replied without a code block. Try again."
    return out
