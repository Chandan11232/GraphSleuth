"""Integration tests: need a running FalkorDB (docker run -p 6379:6379 falkordb/falkordb)."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import requires_falkordb

pytestmark = requires_falkordb

TRACEBACK = '''Refunding an order that does not exist crashes with a 500.

Traceback (most recent call last):
  File "shop/api/handlers.py", line 14, in handle_refund_request
  File "shop/payments/refunds.py", line 17, in process_refund
  File "shop/payments/refunds.py", line 9, in validate_refund_amount
TypeError: 'NoneType' object is not subscriptable'''


@pytest.fixture(scope="module")
def graph(demo_repo):
    from app.core.falkordb import get_falkordb
    from app.services.code_graph import ingest_source

    name = f"repo_test_{uuid.uuid4().hex[:8]}"
    stats = ingest_source(demo_repo, name, "test-repo", "acme-shop")
    yield name, stats
    get_falkordb().delete_graph(name)


def test_ingestion_builds_the_graph(graph):
    _, stats = graph
    assert stats["functions"] >= 20
    assert stats["tests"] == 4
    assert stats["commits"] == 9
    assert stats["people"] == 4
    assert stats["calls"] > 15
    assert stats["commit_function_links"] > 0


def test_graph_tools(graph):
    from app.services.graph_tools import GraphTools

    tools = GraphTools(graph[0])
    callers = tools.get_callers("process_refund")
    assert [c["name"] for c in callers.data["callers"]] == ["handle_refund_request"]
    path = tools.call_path("handle_refund_request", "find_order")
    assert [p["name"] for p in path.data] == ["handle_refund_request", "process_refund", "find_order"]
    experts = tools.find_experts("process_refund")
    assert {e["name"] for e in experts.data} == {"Dana Kim", "Priya Raman"}
    assert tools.hotspots(3).data


def test_risky_changes(graph):
    from app.services.graph_tools import GraphTools

    tools = GraphTools(graph[0])
    # The last 3 commits touched process_refund, apply_discount (both tested),
    # notify_refund and send_email (untested, added by Lee).
    risky = tools.risky_changes(3)
    assert {r["function"] for r in risky.data} == {"notify_refund", "send_email"}
    assert all(r["changed_by"] == ["Lee Okafor"] for r in risky.data)
    assert risky.visited == {r["id"] for r in risky.data}
    assert {r["function"]: r["callers"] for r in risky.data} == {"send_email": 1, "notify_refund": 1}

    # Looking further back finds more untested changes, never tested ones.
    older = {r["function"] for r in tools.risky_changes(9).data}
    assert {"notify_refund", "send_email"} < older
    assert not older & {"process_refund", "apply_discount", "cart_total"}


def test_test_context(graph):
    from app.services.graph_tools import GraphTools

    ctx = GraphTools(graph[0]).test_context("update_stock").data
    assert {c["name"] for c in ctx["callers"]} == {"reserve_items", "restock_items"}
    assert "_PRODUCTS = {" in ctx["module_code"]  # module state the test has to read
    # No test calls update_stock, but one exercises a function two calls away.
    assert ctx["examples_are_nearby"]
    assert ctx["example_tests"][0]["file_path"] == "tests/test_refunds.py"
    assert "def test_" in ctx["example_tests"][0]["code"]


async def test_suggest_test_without_a_model(graph):
    from app.services import test_writer

    out = await test_writer.suggest_test(graph[0], "send_email")
    assert out["test_path"] == "tests/test_notifications.py"
    assert out["framework"] == "pytest"
    assert out["context"]["callers"] == [{"name": "notify_refund", "file_path": "shop/notifications.py"}]
    assert out["code"] == "" and "graph-only" in out["error"]
    with pytest.raises(test_writer.FunctionNotFound):
        await test_writer.suggest_test(graph[0], "no_such_function")


def test_parse_test_reply():
    from app.services.test_writer import parse_reply

    reply = "Here it is:\n```python\nimport pytest\n\ndef test_x():\n    assert 1\nCovers: normal case\n```\nCovers: normal case"
    assert parse_reply(reply) == {"code": "import pytest\n\ndef test_x():\n    assert 1", "covers": "normal case"}


async def _run(graph_name, title, description):
    from app.services.agent import Investigation, new_id

    inv = Investigation(graph_name, "test-repo", new_id(), new_id(), title, description, use_llm=False)
    result = None
    async for event in inv.run():
        if event["type"] == "result":
            result = event["result"]
    return result


async def test_agent_finds_root_cause_and_remembers_it(graph):
    from app.core.falkordb import get_falkordb
    from app.services import cypher_queries as Q

    first = await _run(graph[0], "Refund of unknown order returns 500", TRACEBACK)
    assert first["verdict"]["name"] == "process_refund"
    assert all(s["query"] or s["action"] in ("parse_bug_report", "rank_suspects", "verdict", "generate_fix")
               for s in first["steps"])

    get_falkordb().query(graph[0], Q.CONFIRM_ROOT_CAUSE,
                         {"id": first["id"], "fn_id": first["verdict"]["id"], "note": "test"})

    second = await _run(graph[0], "Partial refund crashes for deleted orders",
                        "Partial refunds for deleted orders crash the refund API instead of returning an error.")
    top = second["suspects"][0]
    assert top["name"] == "process_refund"
    assert top["breakdown"]["memory"] == 1.0


def test_api_flow(demo_repo):
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/v1/health").json()["falkordb"] is True
        repo = client.post("/api/v1/repos", json={"name": "acme-shop", "url": demo_repo}).json()
        for _ in range(100):
            repo = client.get(f"/api/v1/repos/{repo['id']}").json()
            if repo["status"] not in ("pending", "ingesting"):
                break
            time.sleep(0.1)
        assert repo["status"] == "ready", repo
        inv = client.post("/api/v1/investigations", json={
            "repository_id": repo["id"], "title": "Refund crash", "description": TRACEBACK, "use_llm": False}).json()
        with client.stream("GET", f"/api/v1/investigations/{inv['id']}/events") as stream:
            lines = [line for line in stream.iter_lines() if line.startswith("data: ")]
        assert lines[-1] == "data: [DONE]"
        result = client.get(f"/api/v1/investigations/{inv['id']}").json()
        assert result["verdict"]["name"] == "process_refund"
        evidence = client.get(f"/api/v1/graph/investigation/{inv['id']}").json()
        assert {"BugReport", "Function", "Commit"} <= {n["type"] for n in evidence["nodes"]}
        assert any(e["type"] == "CALLS" for e in evidence["edges"])
        risky = client.get(f"/api/v1/graph/{repo['id']}/risky-changes", params={"commits": 3}).json()
        suggestion = client.post(f"/api/v1/graph/{repo['id']}/function/{risky[0]['id']}/suggest-test").json()
        assert suggestion["function"]["id"] == risky[0]["id"] and suggestion["context"]["cypher"]
        assert client.post(f"/api/v1/graph/{repo['id']}/function/ffffffffffffffff/suggest-test").status_code == 404
        client.delete(f"/api/v1/repos/{repo['id']}")


def test_rejects_unsafe_clone_urls():
    from app.services.code_graph import _validate_remote_url

    for bad in ["file:///etc", "https://x.com/a;rm -rf", "https://user:pw@github.com/a/b", "ssh://github.com/a"]:
        with pytest.raises(ValueError):
            _validate_remote_url(bad)
    assert _validate_remote_url("https://github.com/pallets/flask")


def test_local_repos_are_limited_to_their_root(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.services.code_graph import ingest_source

    monkeypatch.setattr(settings, "LOCAL_REPOS_ROOT", str(tmp_path / "demo"))
    (tmp_path / "demo").mkdir()
    for outside in [str(tmp_path), str(tmp_path / "demo" / "..")]:
        with pytest.raises(ValueError, match="must be inside"):
            ingest_source(outside, "repo_never_written", "r", "x")


async def test_repo_without_calls_does_not_crash(tmp_path):
    """A repo with no CALLS edges: PageRank must be skipped, and a report that
    matches nothing must still finish cleanly."""
    from app.core.falkordb import get_falkordb
    from app.services.code_graph import ingest_source
    from app.services.graph_tools import GraphTools

    (tmp_path / "util.js").write_text("export function lonely() { return 1 }\n")
    name = f"repo_test_{uuid.uuid4().hex[:8]}"
    try:
        ingest_source(str(tmp_path), name, "r", "lonely")
        assert GraphTools(name).hotspots(5).data == []
        found = await _run(name, "Lonely is broken", "lonely returns the wrong value")
        assert found["verdict"]["name"] == "lonely"
        missing = await _run(name, "Checkout fails", "the payment page shows a spinner forever")
        assert missing["verdict"] == {} and missing["steps"][-2]["action"] == "no_match"
    finally:
        get_falkordb().delete_graph(name)


async def test_plain_english_report_finds_anonymous_handler(tmp_path):
    """No stack trace or identifiers: the handler whose body appends tasks should win."""
    from app.core.falkordb import get_falkordb
    from app.services.code_graph import ingest_source

    (tmp_path / "main.js").write_text('''window.addEventListener("load", () => {
  const form = document.querySelector("#new-task-form");
  const list_el = document.querySelector("#tasks");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const task_el = document.createElement("div");
    task_el.classList.add("task");
    list_el.appendChild(task_el);
    const task_edit_el = document.createElement("button");
    task_edit_el.addEventListener("click", () => { task_edit_el.innerText = "Save"; });
    const task_delete_el = document.createElement("button");
    task_delete_el.addEventListener("click", () => { list_el.removeChild(task_el); });
  });
});
''')
    name = f"repo_test_{uuid.uuid4().hex[:8]}"
    try:
        ingest_source(str(tmp_path), name, "r", "todo")
        result = await _run(name, "the new tasks are not getting append to the main list",
                            "when i am trying to add a new task, its not getting added to the main list.")
        assert result["verdict"]["name"] == "form.addEventListener('submit')"
    finally:
        get_falkordb().delete_graph(name)
