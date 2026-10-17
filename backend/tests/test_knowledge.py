"""Company knowledge: text rules (no database) and the demo story (needs FalkorDB)."""
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import requires_falkordb


def test_closing_keywords_and_references():
    from app.services.knowledge import closing_refs, references

    text = "Fixes #4 and closes: #9. See #12 and owner/repo#3, not issue-7."
    assert closing_refs(text) == [4, 9]
    assert references(text) == [4, 9, 12]


def test_decision_sentences():
    from app.services.knowledge import decision_sentences

    text = ("No: `find_order` returns None. Decision: refunds always use `get_order`. Makes sense, agreed.\n"
            "- We agreed to cap BULK at $25.")
    assert decision_sentences(text) == ["Decision: refunds always use `get_order`.", "We agreed to cap BULK at $25."]
    assert decision_sentences("LGTM, decided nothing worth noting? ok") == ["LGTM, decided nothing worth noting?"]
    assert decision_sentences("Looks good.") == []


def test_mentioned_functions():
    from app.services.knowledge import mentioned_functions

    names = {"process_refund", "checkout", "getOrder", "save"}
    text = "`checkout()` calls process_refund and getOrder; we save often"
    assert mentioned_functions(text, names) == {"checkout", "process_refund", "getOrder"}


def test_codeowners_rules():
    from app.services.knowledge import file_owners, parse_codeowners

    rules = parse_codeowners("""
# comment
*                 @everyone
/shop/payments/   @org/payments
*.md              @docs-person
/shop/orders.py   @org/payments @priya  # inline comment
docs/             @docs-person
""")
    owners = file_owners(rules, ["shop/payments/refunds.py", "shop/orders.py", "README.md", "shop/db.py",
                                 "src/docs/guide.txt", "shop/payments_old.py"])
    assert owners["shop/payments/refunds.py"] == ("/shop/payments/", ["@org/payments"])
    assert owners["shop/orders.py"] == ("/shop/orders.py", ["@org/payments", "@priya"])
    assert owners["README.md"][1] == ["@docs-person"]
    assert owners["shop/db.py"][1] == ["@everyone"]
    assert owners["src/docs/guide.txt"][1] == ["@docs-person"]  # unanchored directory matches at any depth
    assert owners["shop/payments_old.py"][1] == ["@everyone"]


def test_github_slug():
    from app.services.knowledge import github_slug

    assert github_slug("https://github.com/pallets/flask.git") == ("pallets", "flask")
    assert github_slug("https://gitlab.com/a/b") is None
    assert github_slug("/demo/acme-shop") is None


@pytest.fixture(scope="module")
def graph(demo_repo):
    from app.core.falkordb import get_falkordb
    from app.services.code_graph import ingest_source

    name = f"repo_test_{uuid.uuid4().hex[:8]}"
    stats = ingest_source(demo_repo, name, "test-repo", "acme-shop")
    yield name, stats
    get_falkordb().delete_graph(name)


@requires_falkordb
def test_knowledge_is_ingested(graph):
    _, stats = graph
    assert stats["knowledge_issues"] == 4
    assert stats["pull_requests"] == 9
    assert stats["pr_commit_links"] == 9  # every PR in the export matched its commit
    assert stats["decisions"] == 2
    assert stats["owned_files"] == stats["files"]  # CODEOWNERS has a catch-all rule
    assert stats["fixes_links"] == 3


@requires_falkordb
def test_why_code_traces_the_regression(graph):
    from app.services.graph_tools import GraphTools

    why = GraphTools(graph[0]).why_code("process_refund")
    latest = why.data["changes"][0]["pull_request"]
    assert latest["number"] == 12 and latest["opened_by"] == "Dana Kim"
    assert latest["reviews"] == [{"name": "Lee Okafor", "state": "APPROVED"}]
    assert [i["number"] for i in latest["fixes"]] == [10]
    [decision] = why.data["decisions"]
    assert "get_order" in decision["text"] and decision["author"] == "Priya Raman"
    # The decision was made in PR #6; PR #12 changed the code it governs afterwards.
    assert decision["item_number"] == 6
    assert [(d["pr"], d["functions"]) for d in decision["drift"]] == [(12, ["process_refund"])]
    assert {d["number"] for d in why.data["discussions"] if d["state"] == "open"} == {13}
    assert {"pr-12", "pr-6", "issue-13", decision["id"]} <= why.visited


@requires_falkordb
def test_decision_without_later_changes_has_no_drift(graph):
    from app.services.graph_tools import GraphTools

    bulk = next(d for d in GraphTools(graph[0]).decisions().data if "BULK" in d["text"])
    assert [f["name"] for f in bulk["functions"]] == ["apply_discount"]  # through the PR that fixed issue #8
    assert bulk["drift"] == []


@requires_falkordb
def test_who_owns(graph):
    from app.services.graph_tools import GraphTools

    tools = GraphTools(graph[0])
    owners = tools.who_owns("process_refund").data
    assert owners["file"] == "shop/payments/refunds.py"
    assert [t["team"] for t in owners["teams"]] == ["payments"]
    assert {p["name"] for p in owners["ask"][:2]} == {"Dana Kim", "Priya Raman"}
    assert tools.who_owns("shop/notifications.py").data["owners"][0]["name"] == "Lee Okafor"
    assert tools.who_owns("nothing_like_this").error == "not_found"


@requires_falkordb
async def test_brain_answers_with_sources(graph):
    from app.services.brain import Brain

    out = await Brain(graph[0]).ask("Why does process_refund use find_order?", use_llm=False)
    assert "PR #12" in out["answer"] and "check whether the decision still holds" in out["answer"]
    assert "None of its approvers is in @payments" in out["answer"]
    types = {(s["type"], s.get("number")) for s in out["sources"]}
    assert {("PullRequest", 12), ("Decision", 6), ("Issue", 13)} <= types
    # Every [n] in the answer points at a listed source.
    import re
    assert {int(n) for n in re.findall(r"\[(\d+)\]", out["answer"])} <= {s["n"] for s in out["sources"]}

    who = await Brain(graph[0]).ask("Who should review a change to shop/payments/refunds.py?", use_llm=False)
    assert who["answer"].startswith("`shop/payments/refunds.py` is owned by @payments")


@requires_falkordb
async def test_investigation_includes_history_and_owners(graph):
    from tests.test_investigation import TRACEBACK, _run

    result = await _run(graph[0], "Refund of unknown order returns 500", TRACEBACK)
    assert result["verdict"]["name"] == "process_refund"
    assert result["fix"]["history"]["changes"][0]["pull_request"]["number"] == 12
    assert result["fix"]["owners"]["teams"][0]["team"] == "payments"
    assert {"pr-12", "team-payments"} <= set(result["evidence_ids"])


@requires_falkordb
def test_brain_api(demo_repo):
    from app.main import app

    with TestClient(app) as client:
        repo = client.post("/api/v1/repos", json={"name": "acme-shop", "url": demo_repo}).json()
        for _ in range(100):
            repo = client.get(f"/api/v1/repos/{repo['id']}").json()
            if repo["status"] not in ("pending", "ingesting"):
                break
            time.sleep(0.1)
        assert repo["status"] == "ready", repo
        out = client.post(f"/api/v1/brain/{repo['id']}/ask",
                          json={"question": "Why does process_refund use find_order?", "use_llm": False}).json()
        assert out["sources"] and out["graph"]["nodes"]
        assert {"PullRequest", "Decision", "Function"} <= {n["type"] for n in out["graph"]["nodes"]}
        overview = client.get(f"/api/v1/brain/{repo['id']}/overview").json()
        assert overview["status"]["source"] == "file" and overview["status"]["decisions"] == 2
        assert any(d["drift"] for d in overview["decisions"])
        synced = client.post(f"/api/v1/brain/{repo['id']}/sync").json()
        assert synced["since"] and synced["stats"]["decisions"] == 2  # idempotent: nothing duplicated
        assert client.get(f"/api/v1/brain/{repo['id']}/overview").json()["status"]["decisions"] == 2
        client.delete(f"/api/v1/repos/{repo['id']}")
